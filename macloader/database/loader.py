"""Database loader and registry for declarative models, components, macOS profiles, and dependency catalog."""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union
import yaml

from macloader.database.schema import (
    ComponentSchema,
    DependencyCatalogSchema,
    ModelSchema,
    MacOsSchema,
)
from macloader.domain.dependencies import DependencySpec
from macloader.exceptions import DatabaseNotFoundError, DatabaseValidationError

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).parent / "data"


class Database:
    """In-memory loaded and validated database registry."""

    def __init__(self, data_dir: Optional[Union[str, Path]] = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.models: Dict[str, ModelSchema] = {}
        self.components: Dict[str, ComponentSchema] = {}
        self.macos_profiles: Dict[str, MacOsSchema] = {}
        self.dependency_catalog: Optional[DependencyCatalogSchema] = None
        self._load_all()

    def _load_yaml(self, path: Path) -> dict:
        try:
            content = path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                raise DatabaseValidationError(f"File {path} must contain a top-level dictionary")
            return data
        except Exception as e:
            if isinstance(e, DatabaseValidationError):
                raise
            raise DatabaseValidationError(f"Failed to parse YAML file {path}: {e}") from e

    def _load_all(self) -> None:
        if not self.data_dir.is_dir():
            raise DatabaseNotFoundError(f"Database data directory not found: {self.data_dir}")

        # 1. Load models
        models_dir = self.data_dir / "models"
        if models_dir.is_dir():
            for f in sorted(models_dir.glob("*.yaml")):
                data = self._load_yaml(f)
                model = ModelSchema.validate_and_load(data, filename=f.name)
                self.models[model.id] = model

        # 2. Load components
        comp_dir = self.data_dir / "components"
        if comp_dir.is_dir():
            for f in sorted(comp_dir.glob("*.yaml")):
                data = self._load_yaml(f)
                if "components" in data and isinstance(data["components"], list):
                    for item in data["components"]:
                        comp = ComponentSchema.validate_and_load(item, filename=f.name)
                        self.components[comp.id] = comp
                else:
                    comp = ComponentSchema.validate_and_load(data, filename=f.name)
                    self.components[comp.id] = comp

        # 3. Load macOS targets
        macos_dir = self.data_dir / "macos"
        if macos_dir.is_dir():
            for f in sorted(macos_dir.glob("*.yaml")):
                data = self._load_yaml(f)
                os_prof = MacOsSchema.validate_and_load(data, filename=f.name)
                self.macos_profiles[os_prof.id] = os_prof

        # 4. Load Dependency Catalog
        deps_dir = self.data_dir / "dependencies"
        if deps_dir.is_dir():
            for f in sorted(deps_dir.glob("*.yaml")):
                data = self._load_yaml(f)
                if "macloader_dependency_set" in data:
                    self.dependency_catalog = DependencyCatalogSchema.validate_and_load(data, filename=f.name)
                    break

    def get_model(self, model_id: str) -> Optional[ModelSchema]:
        return self.models.get(model_id.lower())

    def get_model_by_machine_type(self, machine_type: str) -> Optional[ModelSchema]:
        mt = machine_type.strip().upper()
        for model in self.models.values():
            if mt in model.machine_types:
                return model
        return None

    def get_component(self, component_id: str) -> Optional[ComponentSchema]:
        return self.components.get(component_id)

    def get_components_by_category(self, category: str) -> List[ComponentSchema]:
        return [c for c in self.components.values() if c.category == category]

    def get_macos(self, os_name: str) -> Optional[MacOsSchema]:
        return self.macos_profiles.get(os_name.strip().lower())

    def list_supported_macos(self) -> List[str]:
        return sorted(list(self.macos_profiles.keys()))

    def get_dependency_catalog(self) -> Optional[DependencyCatalogSchema]:
        return self.dependency_catalog

    def get_dependency_spec(self, dep_id: str) -> Optional[DependencySpec]:
        if not self.dependency_catalog:
            return None
        return self.dependency_catalog.dependencies.get(dep_id.lower())

    def list_dependency_specs(self) -> List[DependencySpec]:
        if not self.dependency_catalog:
            return []
        return list(self.dependency_catalog.dependencies.values())


# Global database cache
_db_instance: Optional[Database] = None


def get_database(data_dir: Optional[Union[str, Path]] = None, reload: bool = False) -> Database:
    """Obtain the singleton Database instance or create a new one."""
    global _db_instance
    if _db_instance is None or reload or data_dir is not None:
        _db_instance = Database(data_dir=data_dir)
    return _db_instance
