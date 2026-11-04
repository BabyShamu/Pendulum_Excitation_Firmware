"""Background experiment-list loading for the Experiment Manager.

Reading every manifest from disk blocks the Qt UI thread, so the scan runs in
a QThread and delivers results through a queued signal. A monotonically
increasing generation token guarantees that a superseded or cancelled scan can
never overwrite newer results, and the page can be destroyed while a scan is
in flight without the worker touching it.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from pendulum_workbench.experiments.repository import ExperimentRepository


class ExperimentListLoader(QThread):
    """Load experiment manifests off the UI thread.

    ``finished`` carries ``(generation, records)`` where ``records`` is the
    same list of ``(directory, manifest)`` tuples that
    :meth:`ExperimentRepository.list_manifests` returns. The receiver compares
    ``generation`` against the token it last issued and ignores stale scans.
    """

    finished = Signal(int, object)

    def __init__(self, root: Path, generation: int) -> None:
        # Parentless by design: the loader must outlive the page while running
        # so that a queued signal never targets a destroyed widget. The page
        # calls stop_and_wait() before it is destroyed, then deleteLater().
        super().__init__(None)
        self._root = Path(root)
        self._generation = generation
        self._cancelled = False

    @property
    def generation(self) -> int:
        return self._generation

    def cancel(self) -> None:
        # Cooperative: the scan is short, so this only suppresses delivery.
        self._cancelled = True

    def stop_and_wait(self, timeout_ms: int = 5000) -> None:
        """Cancel delivery and block until the scan finishes.

        Safe to call from the UI thread while the page is being destroyed;
        the worker never touches the page after this returns.
        """
        self.cancel()
        if self.isRunning():
            self.wait(timeout_ms)

    def run(self) -> None:
        records = ExperimentRepository(self._root).list_manifests()
        if not self._cancelled:
            self.finished.emit(self._generation, records)
