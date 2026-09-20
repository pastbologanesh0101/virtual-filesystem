"""VirtualFileSystem: an in-memory hierarchical filesystem.

Everything lives in a single process's memory: a table of Inode objects
keyed by an integer inode id, starting from a root directory (id 0). There
is no real disk I/O involved in the data model itself -- the only place
real files are touched is the optional JSON save()/load() persistence
layer, which is a snapshot mechanism, not a backing store.

Path resolution rules
----------------------
- Paths use ``/`` as the separator, exactly like Unix.
- A path beginning with ``/`` is absolute (resolved from the root).
- Any other path is relative to the current working directory.
- ``.`` refers to the current component, ``..`` to its parent (``..`` of
  root is root, same as real Unix).
- Repeated slashes and empty components (from a trailing slash) are
  ignored, so ``/a//b/`` == ``/a/b``.
- Resolving a path that walks *through* a non-directory component (e.g.
  ``/file.txt/subpath``) raises ``NotADirectoryError`` naming the exact
  offending component -- it never silently creates or corrupts anything.
"""

from __future__ import annotations

import fnmatch
import json
import time
from typing import Dict, List, Optional, Tuple

from .inode import (
    DEFAULT_DIR_MODE,
    DEFAULT_FILE_MODE,
    EXECUTE,
    Inode,
    InodeType,
    READ,
    WRITE,
)

ROOT_ID = 0


class VirtualFileSystem:
    def __init__(self, user: str = "root", group: str = "root") -> None:
        self.current_user = user
        self.current_group = group
        self.inodes: Dict[int, Inode] = {}
        self._next_id = 0

        root = self._new_inode(
            itype=InodeType.DIRECTORY,
            name="/",
            mode=DEFAULT_DIR_MODE,
            parent_id=None,
        )
        assert root.id == ROOT_ID
        # root is its own parent, matching real Unix semantics for ".."
        root.parent_id = ROOT_ID

        self.cwd_id = ROOT_ID

    # -- inode table bookkeeping ------------------------------------------

    def _alloc_id(self) -> int:
        new_id = self._next_id
        self._next_id += 1
        return new_id

    def _new_inode(
        self,
        itype: InodeType,
        name: str,
        mode: int,
        parent_id: Optional[int],
        owner: Optional[str] = None,
        group: Optional[str] = None,
    ) -> Inode:
        inode_id = self._alloc_id()
        inode = Inode(
            inode_id=inode_id,
            itype=itype,
            name=name,
            owner=owner or self.current_user,
            group=group or self.current_group,
            mode=mode,
            parent_id=parent_id,
        )
        self.inodes[inode_id] = inode
        return inode

    # -- permission checks --------------------------------------------------

    def _perm_bits_for(self, inode: Inode) -> int:
        if self.current_user == inode.owner:
            return (inode.mode >> 6) & 0o7
        if self.current_group == inode.group:
            return (inode.mode >> 3) & 0o7
        return inode.mode & 0o7

    def check_permission(self, inode: Inode, need: int, action: str = "access") -> None:
        bits = self._perm_bits_for(inode)
        if bits & need != need:
            missing = []
            if need & READ and not bits & READ:
                missing.append("read")
            if need & WRITE and not bits & WRITE:
                missing.append("write")
            if need & EXECUTE and not bits & EXECUTE:
                missing.append("execute")
            raise PermissionError(
                f"cannot {action} '{inode.name}': permission denied "
                f"(missing {'/'.join(missing)} permission for "
                f"{'owner' if self.current_user == inode.owner else 'other'} "
                f"on mode {oct(inode.mode)})"
            )

    # -- path splitting / resolution -----------------------------------------

    @staticmethod
    def _split(path: str) -> Tuple[bool, List[str]]:
        if path == "":
            return False, []
        absolute = path.startswith("/")
        parts = [p for p in path.split("/") if p not in ("", ".")]
        return absolute, parts

    def _resolve(self, path: str, base_id: int, resolve_final: bool = True) -> int:
        """Resolve `path` starting at `base_id`, returning the final inode id.

        Raises FileNotFoundError / NotADirectoryError with a message that
        names the exact failing path component.
        """
        absolute, parts = self._split(path)
        current_id = ROOT_ID if absolute else base_id

        for part in parts:
            current = self.inodes[current_id]
            if not current.is_dir:
                raise NotADirectoryError(
                    f"'{self._path_of(current_id)}' is not a directory "
                    f"(while resolving '{path}')"
                )
            if part == "..":
                current_id = current.parent_id
                continue
            if part not in current.children:
                raise FileNotFoundError(
                    f"no such file or directory: '{part}' in "
                    f"'{self._path_of(current_id)}' (while resolving '{path}')"
                )
            current_id = current.children[part]

        return current_id

    def _resolve_parent(self, path: str) -> Tuple[Inode, str]:
        """Resolve the parent directory of `path` and return (parent, basename).

        Used by creation operations (mkdir/touch/write of a new file).
        """
        absolute, parts = self._split(path)
        if not parts:
            raise IsADirectoryError(f"'{path}' is not a valid target name")
        *dirparts, basename = parts
        if basename in (".", ".."):
            raise OSError(f"'{path}' does not name a creatable entry")
        base_id = ROOT_ID if absolute else self.cwd_id
        dir_path = "/".join(dirparts)
        parent_id = self._resolve(dir_path, base_id) if dirparts or absolute else (
            ROOT_ID if absolute else self.cwd_id
        )
        parent = self.inodes[parent_id]
        if not parent.is_dir:
            raise NotADirectoryError(f"'{dir_path}' is not a directory")
        return parent, basename

    def resolve(self, path: str) -> Inode:
        """Public: resolve any path (absolute or relative to cwd) to its Inode."""
        return self.inodes[self._resolve(path, self.cwd_id)]

    def _path_of(self, inode_id: int) -> str:
        if inode_id == ROOT_ID:
            return "/"
        names = []
        current_id = inode_id
        seen = set()
        while current_id != ROOT_ID:
            if current_id in seen:
                break  # defensive: never loop forever on a corrupted graph
            seen.add(current_id)
            inode = self.inodes[current_id]
            names.append(inode.name)
            current_id = inode.parent_id
        return "/" + "/".join(reversed(names))

    def getcwd(self) -> str:
        return self._path_of(self.cwd_id)

    def normalize(self, path: str) -> str:
        """Return the absolute, normalized string form of `path` (no existence check needed
        beyond directories walked through)."""
        inode_id = self._resolve(path, self.cwd_id)
        return self._path_of(inode_id)

    # -- directory navigation -------------------------------------------------

    def chdir(self, path: str) -> None:
        target_id = self._resolve(path, self.cwd_id)
        target = self.inodes[target_id]
        if not target.is_dir:
            raise NotADirectoryError(f"'{path}' is not a directory")
        self.check_permission(target, EXECUTE, action="enter directory")
        self.cwd_id = target_id

    def listdir(self, path: str = ".") -> List[Inode]:
        target = self.resolve(path) if path != "." else self.inodes[self.cwd_id]
        if not target.is_dir:
            raise NotADirectoryError(f"'{path}' is not a directory")
        self.check_permission(target, READ, action="list")
        names = sorted(target.children)
        return [self.inodes[target.children[name]] for name in names]

    # -- creation --------------------------------------------------------------

    def mkdir(self, path: str, mode: int = DEFAULT_DIR_MODE, parents: bool = False) -> Inode:
        if parents:
            return self._mkdir_p(path, mode)
        parent, name = self._resolve_parent(path)
        if not name:
            raise FileExistsError(f"'{path}' is not a valid directory name")
        if name in parent.children:
            raise FileExistsError(f"'{path}' already exists")
        self.check_permission(parent, WRITE, action="create directory in")
        new_dir = self._new_inode(InodeType.DIRECTORY, name, mode, parent.id)
        parent.children[name] = new_dir.id
        parent.touch_modified()
        return new_dir

    def _mkdir_p(self, path: str, mode: int) -> Inode:
        absolute, parts = self._split(path)
        current_id = ROOT_ID if absolute else self.cwd_id
        node = self.inodes[current_id]
        for part in parts:
            if part == "..":
                current_id = node.parent_id
                node = self.inodes[current_id]
                continue
            if not node.is_dir:
                raise NotADirectoryError(f"'{node.name}' is not a directory (while making '{path}')")
            if part in node.children:
                current_id = node.children[part]
                node = self.inodes[current_id]
                continue
            self.check_permission(node, WRITE, action="create directory in")
            new_dir = self._new_inode(InodeType.DIRECTORY, part, mode, node.id)
            node.children[part] = new_dir.id
            node.touch_modified()
            current_id = new_dir.id
            node = new_dir
        return node

    def touch(self, path: str, mode: int = DEFAULT_FILE_MODE) -> Inode:
        parent, name = self._resolve_parent(path)
        if not name:
            raise IsADirectoryError(f"'{path}' is not a valid file name")
        if name in parent.children:
            existing = self.inodes[parent.children[name]]
            existing.touch_modified()
            return existing
        self.check_permission(parent, WRITE, action="create file in")
        new_file = self._new_inode(InodeType.FILE, name, mode, parent.id)
        parent.children[name] = new_file.id
        parent.touch_modified()
        return new_file

    # -- file content ------------------------------------------------------

    def write_file(self, path: str, data: bytes, append: bool = False) -> Inode:
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            inode = self.resolve(path)
        except FileNotFoundError:
            inode = self.touch(path)
        if not inode.is_file:
            raise IsADirectoryError(f"'{path}' is a directory, cannot write file content")
        self.check_permission(inode, WRITE, action="write")
        inode.content = (inode.content + data) if append else data
        inode.touch_modified()
        return inode

    def read_file(self, path: str) -> bytes:
        inode = self.resolve(path)
        if not inode.is_file:
            raise IsADirectoryError(f"'{path}' is a directory, cannot read as a file")
        self.check_permission(inode, READ, action="read")
        return inode.content

    # -- removal -------------------------------------------------------------

    def _detach(self, inode: Inode) -> Inode:
        parent = self.inodes[inode.parent_id]
        self.check_permission(parent, WRITE, action="remove entry from")
        del parent.children[inode.name]
        parent.touch_modified()
        return inode

    def _free_subtree(self, inode: Inode) -> None:
        """Recursively delete `inode` and, if a directory, all descendants,
        freeing every inode id involved. Does NOT touch the parent's
        children mapping -- callers detach first."""
        if inode.is_dir:
            for child_id in list(inode.children.values()):
                self._free_subtree(self.inodes[child_id])
        del self.inodes[inode.id]

    def remove_file(self, path: str) -> None:
        inode = self.resolve(path)
        if inode.is_dir:
            raise IsADirectoryError(f"'{path}' is a directory; use rmdir")
        self._detach(inode)
        del self.inodes[inode.id]

    def rmdir(self, path: str, recursive: bool = False) -> None:
        inode = self.resolve(path)
        if not inode.is_dir:
            raise NotADirectoryError(f"'{path}' is not a directory")
        if inode.id == ROOT_ID:
            raise PermissionError("cannot remove the root directory")
        if inode.children and not recursive:
            raise OSError(f"directory '{path}' is not empty (use recursive=True / rmdir -r)")
        self.check_permission(inode, WRITE, action="remove contents of")
        self._detach(inode)
        self._free_subtree(inode)
        if self.cwd_id not in self.inodes:
            # cwd was inside the removed subtree: fall back to root, like a
            # shell whose directory was deleted out from under it.
            self.cwd_id = ROOT_ID

    # -- move / rename --------------------------------------------------------

    def _is_ancestor(self, maybe_ancestor_id: int, of_id: int) -> bool:
        current = of_id
        seen = set()
        while current != ROOT_ID:
            if current in seen:
                break
            seen.add(current)
            if current == maybe_ancestor_id:
                return True
            current = self.inodes[current].parent_id
        return current == maybe_ancestor_id

    def move(self, src: str, dst: str) -> Inode:
        src_inode = self.resolve(src)
        old_parent = self.inodes[src_inode.parent_id]

        # Determine destination parent + final name, mirroring `mv` semantics:
        # moving into an existing directory keeps the source's basename.
        try:
            dst_inode = self.resolve(dst)
        except FileNotFoundError:
            dst_inode = None

        if dst_inode is not None and dst_inode.is_dir:
            new_parent = dst_inode
            new_name = src_inode.name
        else:
            new_parent, new_name = self._resolve_parent(dst)
            if new_name in new_parent.children and new_parent.children[new_name] != src_inode.id:
                raise FileExistsError(f"'{dst}' already exists")

        if src_inode.is_dir and (new_parent.id == src_inode.id or self._is_ancestor(src_inode.id, new_parent.id)):
            raise OSError("cannot move a directory into itself or one of its own descendants")

        if new_name in new_parent.children and self.inodes[new_parent.children[new_name]] is src_inode:
            return src_inode  # moving onto itself: no-op

        self.check_permission(old_parent, WRITE, action="move entry out of")
        self.check_permission(new_parent, WRITE, action="move entry into")

        del old_parent.children[src_inode.name]
        old_parent.touch_modified()

        src_inode.name = new_name
        src_inode.parent_id = new_parent.id
        new_parent.children[new_name] = src_inode.id
        new_parent.touch_modified()
        src_inode.touch_modified()
        return src_inode

    # -- copy ------------------------------------------------------------------

    def _clone_subtree(self, inode: Inode, new_name: str, new_parent_id: int) -> Inode:
        clone = self._new_inode(
            itype=inode.type,
            name=new_name,
            mode=inode.mode,
            parent_id=new_parent_id,
            owner=self.current_user,
            group=self.current_group,
        )
        if inode.is_file:
            clone.content = bytes(inode.content)  # independent copy of the bytes
        else:
            for child_name, child_id in inode.children.items():
                child = self.inodes[child_id]
                child_clone = self._clone_subtree(child, child_name, clone.id)
                clone.children[child_name] = child_clone.id
        return clone

    def copy(self, src: str, dst: str, recursive: bool = False) -> Inode:
        src_inode = self.resolve(src)
        if src_inode.is_dir and not recursive:
            raise IsADirectoryError(f"'{src}' is a directory; use recursive=True / cp -r")
        self.check_permission(src_inode, READ, action="copy")

        try:
            dst_inode = self.resolve(dst)
        except FileNotFoundError:
            dst_inode = None

        if dst_inode is not None and dst_inode.is_dir:
            new_parent = dst_inode
            new_name = src_inode.name
        else:
            new_parent, new_name = self._resolve_parent(dst)

        if new_name in new_parent.children:
            raise FileExistsError(f"'{dst}' already exists")

        self.check_permission(new_parent, WRITE, action="copy into")
        clone = self._clone_subtree(src_inode, new_name, new_parent.id)
        new_parent.children[new_name] = clone.id
        new_parent.touch_modified()
        return clone

    # -- chmod -------------------------------------------------------------

    def chmod(self, path: str, mode: int) -> Inode:
        inode = self.resolve(path)
        if self.current_user != inode.owner:
            raise PermissionError(f"cannot chmod '{path}': not the owner")
        inode.mode = mode
        inode.touch_modified()
        return inode

    # -- du / find ----------------------------------------------------------

    def du(self, path: str = ".") -> int:
        inode = self.resolve(path) if path != "." else self.inodes[self.cwd_id]
        return self._du_recursive(inode)

    def _du_recursive(self, inode: Inode) -> int:
        if inode.is_file:
            return inode.size
        total = 0
        for child_id in inode.children.values():
            total += self._du_recursive(self.inodes[child_id])
        return total

    def find(self, pattern: str, path: str = ".") -> List[str]:
        start = self.resolve(path) if path != "." else self.inodes[self.cwd_id]
        matches: List[str] = []
        self._find_recursive(start, pattern, matches)
        return matches

    def _find_recursive(self, inode: Inode, pattern: str, matches: List[str]) -> None:
        if inode.is_dir:
            for name, child_id in inode.children.items():
                child = self.inodes[child_id]
                if fnmatch.fnmatch(name, pattern):
                    matches.append(self._path_of(child.id))
                self._find_recursive(child, pattern, matches)

    # -- persistence ---------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "next_id": self._next_id,
            "cwd_id": self.cwd_id,
            "current_user": self.current_user,
            "current_group": self.current_group,
            "saved_at": time.time(),
            "inodes": {str(iid): inode.to_dict() for iid, inode in self.inodes.items()},
        }

    def save(self, filepath: str) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "VirtualFileSystem":
        fs = cls.__new__(cls)
        fs.current_user = data.get("current_user", "root")
        fs.current_group = data.get("current_group", "root")
        fs._next_id = data["next_id"]
        fs.cwd_id = data.get("cwd_id", ROOT_ID)
        fs.inodes = {}
        for iid_str, inode_data in data["inodes"].items():
            inode = Inode.from_dict(inode_data)
            fs.inodes[inode.id] = inode
        if fs.cwd_id not in fs.inodes:
            fs.cwd_id = ROOT_ID
        return fs

    @classmethod
    def load(cls, filepath: str) -> "VirtualFileSystem":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
