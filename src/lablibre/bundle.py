"""
Bundles: everything an emitter produces for one run.

An OT-2 bundle is typically a protocol ``.py`` plus a setup sheet. A Hamilton
bundle is a worklist CSV plus a setup sheet naming the template VENUS method.
Either may also carry data files for chaining protocols, such as the plasmid
locations a transformation protocol needs from an assembly run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import List, Optional, Union


class FileRole(str, Enum):
    PROTOCOL = "protocol"  # a file the robot runs directly (e.g. an Opentrons .py)
    WORKLIST = "worklist"  # data a fixed robot method consumes (e.g. a Hamilton CSV)
    SETUP = "setup"        # instructions for the person setting up the deck
    DATA = "data"          # machine-readable outputs, e.g. well locations for the next protocol


@dataclass(frozen=True)
class BundleFile:
    """One file in a bundle. *path* is relative to the bundle directory and uses ``/`` separators."""
    path: str
    content: Union[str, bytes]
    role: FileRole

    def __post_init__(self):
        object.__setattr__(self, "role", FileRole(self.role))
        pure = PurePosixPath(self.path)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise ValueError(f"Bundle file path must be relative and stay inside the bundle: '{self.path}'")


@dataclass
class Bundle:
    """The files produced by compiling one plan for one robot profile, plus anything worth checking before a run."""
    target: str
    files: List[BundleFile] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add(self, path: str, content: Union[str, bytes], role: Union[FileRole, str]) -> BundleFile:
        if self.get(path) is not None:
            raise ValueError(f"Bundle already contains '{path}'")
        bundle_file = BundleFile(path, content, FileRole(role))
        self.files.append(bundle_file)
        return bundle_file

    def get(self, path: str) -> Optional[BundleFile]:
        return next((f for f in self.files if f.path == path), None)

    def by_role(self, role: Union[FileRole, str]) -> List[BundleFile]:
        role = FileRole(role)
        return [f for f in self.files if f.role is role]

    def write(self, directory: Union[str, Path], overwrite: bool = True) -> List[Path]:
        """
        Write every file under *directory*, creating it if needed.

        Args:
            directory: Output directory.
            overwrite: If ``False``, raise :class:`FileExistsError` instead of replacing existing files.

        Returns:
            The paths written.
        """
        root = Path(directory)
        targets = [(root / f.path, f) for f in self.files]
        if not overwrite:
            existing = [str(path) for path, _ in targets if path.exists()]
            if existing:
                raise FileExistsError(f"Refusing to overwrite: {existing}")
        written = []
        for path, bundle_file in targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(bundle_file.content, bytes):
                path.write_bytes(bundle_file.content)
            else:
                path.write_text(bundle_file.content)
            written.append(path)
        return written
