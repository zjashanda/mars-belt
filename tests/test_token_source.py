"""test: LISTENAI_TOKEN 环境变量优先于 TOOLS.md"""
import os, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "py"))
import listenai_task_support as sup


class TokenSourceTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("LISTENAI_TOKEN")
        self.tmp = sup.LOCAL_TOOLS_MD
        self.backup = self.tmp.read_text(encoding="utf-8") if self.tmp.exists() else None
        self.tmp.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("LISTENAI_TOKEN", None)
        else:
            os.environ["LISTENAI_TOKEN"] = self._old
        if self.backup is None:
            self.tmp.unlink(missing_ok=True)
        else:
            self.tmp.write_text(self.backup, encoding="utf-8")

    def test_env_wins_over_file(self):
        self.tmp.write_text("LISTENAI_TOKEN=from-file\n", encoding="utf-8")
        os.environ["LISTENAI_TOKEN"] = "from-env"
        self.assertEqual(sup.load_local_listenai_token(), "from-env")

    def test_file_is_fallback(self):
        self.tmp.write_text("LISTENAI_TOKEN=from-file\n", encoding="utf-8")
        os.environ.pop("LISTENAI_TOKEN", None)
        self.assertEqual(sup.load_local_listenai_token(), "from-file")


if __name__ == "__main__":
    unittest.main()
