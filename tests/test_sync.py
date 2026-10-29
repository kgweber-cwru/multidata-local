import sqlite3

import pytest

np = pytest.importorskip("numpy")

from multidata import manifest, sync  # noqa: E402

SR = 16000


def _noise(seconds, seed=0):
    return np.random.default_rng(seed).standard_normal(int(seconds * SR)).astype(np.float32) * 0.1


def _shifted(x, lag_s):
    """x as heard by a camera whose file started lag_s earlier: every sound
    lands lag_s later in its file."""
    k = int(round(lag_s * SR))
    return np.concatenate([np.zeros(k, np.float32), x])[:len(x)] if k >= 0 else x[-k:]


class TestMeasureLag:
    @pytest.mark.parametrize("lag_s", [0.0, 0.832, -0.768, 3.5])
    def test_recovers_a_fixed_offset(self, lag_s):
        ref = _noise(200)
        m = sync.measure_lag(ref, _shifted(ref, lag_s), SR)
        assert m["lag_s"] == pytest.approx(lag_s, abs=1 / SR)
        assert m["agreement"] == 1.0

    def test_offset_is_the_negative_lag(self, tmp_path):
        import wave
        ref = _noise(200)
        paths = []
        for name, x in (("a", ref), ("b", _shifted(ref, 0.5))):
            p = tmp_path / f"{name}.wav"
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
                w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
            paths.append(p)
        offset, _ = sync.offset_from_audio(*paths)
        # b's file started 0.5 s before a's: b's time 1.0 s is a's time 0.5 s
        assert offset == pytest.approx(-0.5, abs=1e-3)

    def test_unrelated_audio_does_not_agree(self):
        # two independent recordings -- like room 11's gappy camera 16:
        # windows give scattered lags, so no offset should be trusted
        m = sync.measure_lag(_noise(200, 1), _noise(200, 2), SR)
        assert m["lag_s"] is None or m["agreement"] < sync.MIN_AGREEMENT

    def test_silence_gives_no_offset(self):
        z = np.zeros(200 * SR, np.float32)
        assert sync.measure_lag(z, z, SR)["lag_s"] is None


class TestFrameTimes:
    def test_dropped_counts_gaps(self):
        t = np.arange(100) / 30.0
        t = np.delete(t, range(40, 70))  # 30 frames = 1 s lost
        d = sync.dropped(t)
        assert d["gaps"] == 1
        assert d["lost_s"] == pytest.approx(1.0, abs=1e-6)

    def test_reference_times_adds_offset(self):
        t = np.arange(5) / 30.0
        assert np.allclose(sync.reference_times(t, -0.5), t - 0.5)

    def test_reference_times_refuses_unmeasured_offset(self):
        with pytest.raises(ValueError, match="no sync offset"):
            sync.reference_times(np.arange(5.0), None)

    def test_reference_times_refuses_frame_count_mismatch(self):
        with pytest.raises(ValueError, match="data frames"):
            sync.reference_times(np.arange(5.0), 0.0, n_frames=6)

    def test_nearest_frames_pairs_by_time_across_a_gap(self):
        a = np.arange(10) / 10.0
        b = np.delete(np.arange(10) / 10.0, [4, 5, 6])  # b dropped 0.4-0.6 s
        j, dt = sync.nearest_frames(a, b, max_gap_s=0.05)
        assert j[3] == 3 and j[7] == 4  # b's index 4 is time 0.7
        assert (j[4:7] == -1).all()

    def test_save_load_round_trip(self, tmp_path):
        t = np.array([0.0, 0.0333, 0.0667, 0.2])
        p = sync.save_frame_times(t, tmp_path / "x" / "16.npz")
        assert np.array_equal(sync.load_frame_times(p), t)


class TestManifestColumns:
    def test_migration_adds_sync_columns_to_an_old_videos_table(self, tmp_path):
        db = tmp_path / "old.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE videos (case_id TEXT NOT NULL, camera TEXT NOT NULL, "
                     "pose_status TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY (case_id, camera))")
        conn.execute("INSERT INTO videos (case_id, camera) VALUES ('1', '15')")
        conn.commit(); conn.close()

        conn = manifest.connect(db)
        row = conn.execute("SELECT sync_status, sync_offset_s, sync_agreement FROM videos").fetchone()
        conn.close()
        assert tuple(row) == ("pending", None, None)

    def test_sync_is_a_stage(self, tmp_path):
        db = tmp_path / "m.sqlite"
        conn = manifest.connect(db)
        conn.execute("INSERT INTO cases (case_id) VALUES ('1')")
        conn.commit(); conn.close()
        manifest.add_video("1", "15", path=db)
        assert [r["camera"] for r in manifest.pending("sync", db)] == ["15"]
        v = manifest.update("1", "15", db, sync_status="done", sync_offset_s=-0.832,
                            sync_agreement=1.0)
        assert v.sync_offset_s == -0.832


class TestSnapToPts:
    def test_keeps_only_the_frames_the_decoder_returned(self):
        pts = np.arange(10) / 30.0
        dec = pts[[0, 1, 2, 5, 6, 9]] + 0.0004  # decoder skipped 3, 4, 7, 8
        assert np.allclose(sync.snap_to_pts(dec, pts), pts[[0, 1, 2, 5, 6, 9]])

    def test_repeated_decoder_time_moves_forward(self):
        pts = np.arange(5) / 30.0
        dec = np.array([pts[0], pts[1], pts[1], pts[3]])  # OpenCV repeats a value
        assert np.allclose(sync.snap_to_pts(dec, pts), pts[[0, 1, 2, 3]])
