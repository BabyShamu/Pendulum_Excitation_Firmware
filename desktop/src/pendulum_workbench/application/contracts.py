from pathlib import Path
from typing import Protocol, Sequence


class CommunicationService(Protocol):
    def available_endpoints(self) -> Sequence[str]: ...

    def connect(self, endpoint: str, baud_rate: int) -> None: ...

    def disconnect(self) -> None: ...


class ExperimentService(Protocol):
    def create_experiment(self, name: str, notes: str) -> str: ...

    def load_experiment(self, experiment_id: str) -> Path: ...


class AnalysisService(Protocol):
    def analyze(self, experiment_id: str) -> object: ...


class SimulationService(Protocol):
    def load_results(self, source: Path) -> object: ...


class ValidationService(Protocol):
    def validate(self, experiment_id: str) -> object: ...