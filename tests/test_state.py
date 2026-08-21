# -*- coding: utf-8 -*-
"""Test IO atomic + file lock."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arena.state import InstanceLock, read_json, write_json


class TestAtomicIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_roundtrip(self):
        p = os.path.join(self.tmp, "x.json")
        write_json(p, {"a": [1, 2, 3], "b": "🔥"})
        self.assertEqual(read_json(p), {"a": [1, 2, 3], "b": "🔥"})
        self.assertFalse(os.path.exists(p + ".tmp"))  # không để lại file tạm

    def test_overwrite(self):
        p = os.path.join(self.tmp, "x.json")
        write_json(p, {"v": 1})
        write_json(p, {"v": 2})
        self.assertEqual(read_json(p)["v"], 2)

    def test_read_missing(self):
        self.assertEqual(read_json(os.path.join(self.tmp, "nope.json"), []), [])


class TestInstanceLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_acquire_release(self):
        l1 = InstanceLock("t", lock_dir=self.tmp)
        self.assertTrue(l1.acquire())
        l2 = InstanceLock("t", lock_dir=self.tmp)
        self.assertFalse(l2.acquire())  # đang bị giữ
        l1.release()
        self.assertTrue(l2.acquire())
        l2.release()

    def test_stale_lock_expires(self):
        p = os.path.join(self.tmp, ".t.lock")
        with open(p, "w") as f:
            f.write("old")
        old = time.time() - 9999
        os.utime(p, (old, old))
        l = InstanceLock("t", timeout=900, lock_dir=self.tmp)
        self.assertTrue(l.acquire())
        l.release()

    def test_context_manager(self):
        with InstanceLock("t", lock_dir=self.tmp) as got:
            self.assertTrue(got)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".t.lock")))


if __name__ == "__main__":
    unittest.main()
