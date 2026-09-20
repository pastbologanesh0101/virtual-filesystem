"""Unit tests for vfscore.VirtualFileSystem.

Run with:  python -m pytest tests/ -v
       or:  python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vfscore import VirtualFileSystem
from vfscore.inode import InodeType


def make_fs():
    return VirtualFileSystem(user="alice", group="staff")


class TestCreation(unittest.TestCase):
    def test_mkdir_creates_directory_inode(self):
        fs = make_fs()
        node = fs.mkdir("/docs")
        self.assertEqual(node.type, InodeType.DIRECTORY)
        self.assertEqual(node.name, "docs")
        self.assertEqual(node.owner, "alice")
        self.assertIn("docs", fs.resolve("/").children)

    def test_touch_creates_file_inode(self):
        fs = make_fs()
        node = fs.touch("/note.txt")
        self.assertEqual(node.type, InodeType.FILE)
        self.assertEqual(node.content, b"")
        self.assertEqual(node.size, 0)
        self.assertIn("note.txt", fs.resolve("/").children)

    def test_mkdir_on_existing_path_raises(self):
        fs = make_fs()
        fs.mkdir("/docs")
        with self.assertRaises(FileExistsError):
            fs.mkdir("/docs")


class TestReadWrite(unittest.TestCase):
    def test_write_then_read_round_trips_content(self):
        fs = make_fs()
        fs.touch("/a.txt")
        fs.write_file("/a.txt", b"hello world")
        self.assertEqual(fs.read_file("/a.txt"), b"hello world")

    def test_write_creates_file_if_missing(self):
        fs = make_fs()
        fs.write_file("/new.txt", b"data")
        self.assertEqual(fs.read_file("/new.txt"), b"data")

    def test_append_adds_to_existing_content(self):
        fs = make_fs()
        fs.write_file("/log.txt", b"line1\n")
        fs.write_file("/log.txt", b"line2\n", append=True)
        self.assertEqual(fs.read_file("/log.txt"), b"line1\nline2\n")

    def test_write_updates_size(self):
        fs = make_fs()
        fs.write_file("/a.txt", b"12345")
        self.assertEqual(fs.resolve("/a.txt").size, 5)


class TestListing(unittest.TestCase):
    def test_ls_lists_correct_children_with_metadata(self):
        fs = make_fs()
        fs.mkdir("/proj")
        fs.touch("/proj/readme.txt")
        fs.write_file("/proj/readme.txt", b"abcde")
        fs.mkdir("/proj/src")
        entries = {e.name: e for e in fs.listdir("/proj")}
        self.assertEqual(set(entries), {"readme.txt", "src"})
        self.assertEqual(entries["readme.txt"].size, 5)
        self.assertEqual(entries["readme.txt"].type, InodeType.FILE)
        self.assertEqual(entries["src"].type, InodeType.DIRECTORY)
        self.assertEqual(entries["readme.txt"].owner, "alice")


class TestPathResolutionAndCd(unittest.TestCase):
    def test_cd_absolute_and_relative_with_dot_chains(self):
        fs = make_fs()
        fs.mkdir("/a")
        fs.mkdir("/a/b")
        fs.mkdir("/a/b/c")
        fs.chdir("/a/b/c")
        self.assertEqual(fs.getcwd(), "/a/b/c")

        fs.chdir("../..")
        self.assertEqual(fs.getcwd(), "/a")

        fs.chdir("./b/./c/..")
        self.assertEqual(fs.getcwd(), "/a/b")

        fs.chdir("/")
        self.assertEqual(fs.getcwd(), "/")

    def test_cd_dotdot_past_root_stays_at_root(self):
        fs = make_fs()
        fs.chdir("/../../..")
        self.assertEqual(fs.getcwd(), "/")

    def test_repeated_slashes_normalize_correctly(self):
        fs = make_fs()
        fs.mkdir("/a")
        fs.mkdir("/a/b")
        fs.chdir("//a///b/")
        self.assertEqual(fs.getcwd(), "/a/b")

    def test_path_through_non_directory_errors_clearly(self):
        fs = make_fs()
        fs.touch("/file.txt")
        with self.assertRaises(NotADirectoryError):
            fs.resolve("/file.txt/subpath")
        # the tree must be untouched / not corrupted by the failed attempt
        root = fs.resolve("/")
        self.assertEqual(list(root.children.keys()), ["file.txt"])
        self.assertTrue(fs.resolve("/file.txt").is_file)

    def test_mkdir_through_non_directory_component_errors(self):
        fs = make_fs()
        fs.touch("/file.txt")
        with self.assertRaises(NotADirectoryError):
            fs.mkdir("/file.txt/newdir")


class TestRemoval(unittest.TestCase):
    def test_rm_removes_file_and_frees_inode(self):
        fs = make_fs()
        fs.touch("/a.txt")
        inode_id = fs.resolve("/a.txt").id
        fs.remove_file("/a.txt")
        self.assertNotIn(inode_id, fs.inodes)
        self.assertNotIn("a.txt", fs.resolve("/").children)
        with self.assertRaises(FileNotFoundError):
            fs.resolve("/a.txt")

    def test_rmdir_recursive_removes_entire_subtree(self):
        fs = make_fs()
        fs.mkdir("/proj")
        fs.mkdir("/proj/src")
        fs.touch("/proj/src/main.py")
        fs.mkdir("/proj/src/lib")
        fs.touch("/proj/src/lib/util.py")
        ids_before = [
            fs.resolve(p).id
            for p in ("/proj", "/proj/src", "/proj/src/main.py",
                      "/proj/src/lib", "/proj/src/lib/util.py")
        ]
        fs.rmdir("/proj", recursive=True)
        for iid in ids_before:
            self.assertNotIn(iid, fs.inodes)
        with self.assertRaises(FileNotFoundError):
            fs.resolve("/proj")

    def test_rmdir_non_recursive_on_nonempty_dir_raises(self):
        fs = make_fs()
        fs.mkdir("/proj")
        fs.touch("/proj/file.txt")
        with self.assertRaises(OSError):
            fs.rmdir("/proj")


class TestMoveAndCopy(unittest.TestCase):
    def test_mv_relocates_file_content_preserved(self):
        fs = make_fs()
        fs.mkdir("/dst")
        fs.write_file("/a.txt", b"payload")
        fs.move("/a.txt", "/dst/b.txt")
        with self.assertRaises(FileNotFoundError):
            fs.resolve("/a.txt")
        self.assertEqual(fs.read_file("/dst/b.txt"), b"payload")

    def test_mv_relocates_directory_subtree(self):
        fs = make_fs()
        fs.mkdir("/src")
        fs.mkdir("/src/inner")
        fs.write_file("/src/inner/f.txt", b"x")
        fs.mkdir("/dst")
        fs.move("/src", "/dst/moved")
        with self.assertRaises(FileNotFoundError):
            fs.resolve("/src")
        self.assertEqual(fs.read_file("/dst/moved/inner/f.txt"), b"x")

    def test_cp_creates_independent_copy(self):
        fs = make_fs()
        fs.write_file("/orig.txt", b"original")
        fs.copy("/orig.txt", "/copy.txt")
        fs.write_file("/copy.txt", b"mutated")
        self.assertEqual(fs.read_file("/orig.txt"), b"original")
        self.assertEqual(fs.read_file("/copy.txt"), b"mutated")
        self.assertNotEqual(fs.resolve("/orig.txt").id, fs.resolve("/copy.txt").id)

    def test_cp_recursive_directory_is_independent(self):
        fs = make_fs()
        fs.mkdir("/d1")
        fs.write_file("/d1/f.txt", b"v1")
        fs.copy("/d1", "/d2", recursive=True)
        fs.write_file("/d2/f.txt", b"v2")
        self.assertEqual(fs.read_file("/d1/f.txt"), b"v1")
        self.assertEqual(fs.read_file("/d2/f.txt"), b"v2")
        self.assertNotEqual(fs.resolve("/d1/f.txt").id, fs.resolve("/d2/f.txt").id)

    def test_cp_directory_without_recursive_flag_raises(self):
        fs = make_fs()
        fs.mkdir("/d1")
        with self.assertRaises(IsADirectoryError):
            fs.copy("/d1", "/d2")


class TestPermissions(unittest.TestCase):
    def test_write_to_read_only_file_rejected(self):
        fs = make_fs()
        fs.touch("/ro.txt")
        fs.chmod("/ro.txt", 0o444)
        with self.assertRaises(PermissionError) as ctx:
            fs.write_file("/ro.txt", b"nope")
        self.assertIn("permission", str(ctx.exception).lower())

    def test_other_user_cannot_write_without_permission(self):
        fs = make_fs()
        fs.touch("/priv.txt")
        fs.chmod("/priv.txt", 0o600)
        fs.current_user = "eve"
        fs.current_group = "outsiders"
        with self.assertRaises(PermissionError):
            fs.write_file("/priv.txt", b"hacked")

    def test_owner_can_read_after_permission_change(self):
        fs = make_fs()
        fs.write_file("/f.txt", b"secret")
        fs.chmod("/f.txt", 0o600)
        self.assertEqual(fs.read_file("/f.txt"), b"secret")


class TestDuAndFind(unittest.TestCase):
    def _build_tree(self, fs):
        fs.mkdir("/proj")
        fs.write_file("/proj/a.txt", b"1234567890")       # 10 bytes
        fs.mkdir("/proj/sub")
        fs.write_file("/proj/sub/b.txt", b"12345")         # 5 bytes
        fs.mkdir("/proj/sub/deep")
        fs.write_file("/proj/sub/deep/c.txt", b"123")       # 3 bytes

    def test_du_computes_recursive_size(self):
        fs = make_fs()
        self._build_tree(fs)
        self.assertEqual(fs.du("/proj"), 18)
        self.assertEqual(fs.du("/proj/sub"), 8)

    def test_find_locates_files_across_nested_directories(self):
        fs = make_fs()
        self._build_tree(fs)
        fs.touch("/proj/sub/deep/other.log")
        results = fs.find("*.txt", "/proj")
        self.assertEqual(
            set(results),
            {"/proj/a.txt", "/proj/sub/b.txt", "/proj/sub/deep/c.txt"},
        )
        self.assertNotIn("/proj/sub/deep/other.log", results)


class TestPersistence(unittest.TestCase):
    def test_save_and_load_round_trips_tree_and_content(self):
        fs = make_fs()
        fs.mkdir("/proj")
        fs.write_file("/proj/a.txt", b"hello \x00 bytes")
        fs.mkdir("/proj/sub")
        fs.write_file("/proj/sub/b.txt", b"nested content")
        fs.chmod("/proj/a.txt", 0o600)
        fs.chdir("/proj/sub")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.json")
            fs.save(path)
            restored = VirtualFileSystem.load(path)

        self.assertEqual(restored.read_file("/proj/a.txt"), b"hello \x00 bytes")
        self.assertEqual(restored.read_file("/proj/sub/b.txt"), b"nested content")
        self.assertEqual(restored.resolve("/proj/a.txt").mode, 0o600)
        self.assertEqual(restored.getcwd(), "/proj/sub")
        self.assertEqual(
            sorted(e.name for e in restored.listdir("/proj")),
            ["a.txt", "sub"],
        )


class TestRootAndMisc(unittest.TestCase):
    def test_rmdir_root_forbidden(self):
        fs = make_fs()
        with self.assertRaises(PermissionError):
            fs.rmdir("/", recursive=True)

    def test_mv_into_own_descendant_rejected(self):
        fs = make_fs()
        fs.mkdir("/a")
        fs.mkdir("/a/b")
        with self.assertRaises(OSError):
            fs.move("/a", "/a/b/into")


if __name__ == "__main__":
    unittest.main()
