"""The inode data model for the virtual filesystem.

An Inode is the single unit of storage in the virtual filesystem, exactly
like an inode in a real Unix filesystem: it holds metadata (type, owner,
permissions, timestamps) and either file content or a directory listing.
Names are NOT stored inside the parent's reference to a child by accident --
each directory holds a name -> inode_id mapping, and an Inode also carries
its own `name` purely for convenience (error messages, du/find output) even
though the authoritative name is the key used in the parent's `children`
dict.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Dict, Optional


class InodeType(str, Enum):
    FILE = "file"
    DIRECTORY = "dir"


# Permission bit values, same convention as real Unix mode bits.
READ = 0o4
WRITE = 0o2
EXECUTE = 0o1

DEFAULT_FILE_MODE = 0o644
DEFAULT_DIR_MODE = 0o755


class Inode:
    """A single filesystem object: either a file or a directory.

    Permission model
    -----------------
    Each inode carries a 9-bit Unix-style mode: owner/group/other x rwx,
    identical in spirit to a real ``chmod`` mode (e.g. ``0o644``,
    ``0o755``). The filesystem session has a single active identity
    (``current_user`` / ``current_group``); a permission check compares
    that identity against the inode's ``owner``/``group``:

    - identity matches ``owner``            -> use the owner (high) bits
    - identity's group matches inode.group  -> use the group (middle) bits
    - otherwise                              -> use the other (low) bits

    This is a deliberate simplification of real Unix permissions (no
    supplementary groups, no setuid/setgid/sticky bits) but the bit
    arithmetic is the real thing.
    """

    __slots__ = (
        "id",
        "type",
        "name",
        "owner",
        "group",
        "mode",
        "created_at",
        "modified_at",
        "content",
        "children",
        "parent_id",
    )

    def __init__(
        self,
        inode_id: int,
        itype: InodeType,
        name: str,
        owner: str,
        group: str,
        mode: int,
        parent_id: Optional[int],
        created_at: Optional[float] = None,
        modified_at: Optional[float] = None,
    ) -> None:
        self.id = inode_id
        self.type = itype
        self.name = name
        self.owner = owner
        self.group = group
        self.mode = mode
        self.parent_id = parent_id
        now = time.time()
        self.created_at = created_at if created_at is not None else now
        self.modified_at = modified_at if modified_at is not None else now

        if itype is InodeType.FILE:
            self.content: bytes = b""
            self.children = None
        else:
            self.content = None
            self.children: Dict[str, int] = {}

    # -- convenience -----------------------------------------------------

    @property
    def is_dir(self) -> bool:
        return self.type is InodeType.DIRECTORY

    @property
    def is_file(self) -> bool:
        return self.type is InodeType.FILE

    @property
    def size(self) -> int:
        """Size in bytes. Directories report 0 (see du() for recursive size)."""
        return len(self.content) if self.is_file else 0

    def mode_string(self) -> str:
        """Render mode as an ``ls -l``-style ``rwxr-xr-x`` string."""
        chars = []
        for shift in (6, 3, 0):
            bits = (self.mode >> shift) & 0o7
            chars.append("r" if bits & READ else "-")
            chars.append("w" if bits & WRITE else "-")
            chars.append("x" if bits & EXECUTE else "-")
        prefix = "d" if self.is_dir else "-"
        return prefix + "".join(chars)

    def touch_modified(self) -> None:
        self.modified_at = time.time()

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict:
        import base64

        data = {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "owner": self.owner,
            "group": self.group,
            "mode": self.mode,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }
        if self.is_file:
            data["content"] = base64.b64encode(self.content).decode("ascii")
        else:
            data["children"] = dict(self.children)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Inode":
        import base64

        itype = InodeType(data["type"])
        inode = cls(
            inode_id=data["id"],
            itype=itype,
            name=data["name"],
            owner=data["owner"],
            group=data["group"],
            mode=data["mode"],
            parent_id=data["parent_id"],
            created_at=data.get("created_at"),
            modified_at=data.get("modified_at"),
        )
        if itype is InodeType.FILE:
            inode.content = base64.b64decode(data.get("content", ""))
        else:
            inode.children = dict(data.get("children", {}))
        return inode
