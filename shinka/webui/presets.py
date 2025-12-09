"""Preset manager for saving and loading run configurations."""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Preset:
    """A saved run configuration preset."""

    id: str
    name: str
    description: str
    config: Dict[str, Any]
    created_at: str
    updated_at: str


class PresetManager:
    """Manages preset storage on the backend.

    Presets are stored as JSON files in ~/.shinka/presets/
    """

    def __init__(self, presets_dir: Optional[str] = None):
        """Initialize the preset manager.

        Args:
            presets_dir: Directory to store presets. Defaults to ~/.shinka/presets/
        """
        if presets_dir:
            self.presets_dir = Path(presets_dir)
        else:
            self.presets_dir = Path.home() / ".shinka" / "presets"

        self.presets_dir.mkdir(parents=True, exist_ok=True)

    def list_presets(self) -> List[Dict[str, Any]]:
        """List all available presets.

        Returns:
            List of preset metadata (id, name, description, created_at)
        """
        presets = []
        for file in self.presets_dir.glob("*.json"):
            try:
                with open(file) as f:
                    data = json.load(f)
                    presets.append(
                        {
                            "id": data["id"],
                            "name": data["name"],
                            "description": data.get("description", ""),
                            "created_at": data.get("created_at"),
                            "updated_at": data.get("updated_at"),
                        }
                    )
            except (json.JSONDecodeError, KeyError, IOError):
                continue
        return sorted(presets, key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)

    def get_preset(self, preset_id: str) -> Optional[Preset]:
        """Get a specific preset by ID.

        Args:
            preset_id: The preset ID to retrieve

        Returns:
            Preset object or None if not found
        """
        file_path = self.presets_dir / f"{preset_id}.json"
        if not file_path.exists():
            return None

        try:
            with open(file_path) as f:
                data = json.load(f)
                return Preset(**data)
        except (json.JSONDecodeError, KeyError, IOError):
            return None

    def save_preset(
        self,
        name: str,
        config: Dict[str, Any],
        description: str = "",
        preset_id: Optional[str] = None,
    ) -> Preset:
        """Save a new or update existing preset.

        Args:
            name: Display name for the preset
            config: The configuration dictionary to save
            description: Optional description
            preset_id: Optional ID to update existing preset

        Returns:
            The saved Preset object
        """
        now = datetime.utcnow().isoformat() + "Z"

        if preset_id:
            existing = self.get_preset(preset_id)
            if existing:
                preset = Preset(
                    id=preset_id,
                    name=name,
                    description=description,
                    config=config,
                    created_at=existing.created_at,
                    updated_at=now,
                )
            else:
                preset = Preset(
                    id=preset_id,
                    name=name,
                    description=description,
                    config=config,
                    created_at=now,
                    updated_at=now,
                )
        else:
            preset = Preset(
                id=str(uuid.uuid4())[:8],
                name=name,
                description=description,
                config=config,
                created_at=now,
                updated_at=now,
            )

        file_path = self.presets_dir / f"{preset.id}.json"
        with open(file_path, "w") as f:
            json.dump(asdict(preset), f, indent=2)

        return preset

    def delete_preset(self, preset_id: str) -> bool:
        """Delete a preset.

        Args:
            preset_id: The preset ID to delete

        Returns:
            True if deleted, False if not found
        """
        file_path = self.presets_dir / f"{preset_id}.json"
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def export_preset(self, preset_id: str) -> Optional[str]:
        """Export a preset as a JSON string.

        Args:
            preset_id: The preset ID to export

        Returns:
            JSON string or None if not found
        """
        preset = self.get_preset(preset_id)
        if preset:
            return json.dumps(asdict(preset), indent=2)
        return None

    def import_preset(self, json_str: str, new_name: Optional[str] = None) -> Preset:
        """Import a preset from a JSON string.

        Args:
            json_str: JSON string representing the preset
            new_name: Optional new name for the imported preset

        Returns:
            The imported Preset object
        """
        data = json.loads(json_str)

        # Generate new ID to avoid conflicts
        name = new_name or data.get("name", "Imported Preset")
        config = data.get("config", data)  # Support both full preset and raw config
        description = data.get("description", "")

        return self.save_preset(name=name, config=config, description=description)
