from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen
import xml.etree.ElementTree as ET


INITIALIZR_METADATA_URL = "https://start.spring.io/metadata/client"
GENERIC_TERMS = {
    "database",
    "db",
    "base de datos",
    "seguridad",
    "security",
}


@dataclass(frozen=True)
class DependencyOption:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class ExternalBom:
    group_id: str
    artifact_id: str
    version: str
    min_boot_without_bom: str


EXTERNAL_BOMS: dict[str, ExternalBom] = {
    "spring-ai": ExternalBom(
        group_id="org.springframework.ai",
        artifact_id="spring-ai-bom",
        version="1.0.0",
        min_boot_without_bom="3.4.0",
    )
}


class InitializrMetadataClient:
    def __init__(self, metadata_url: str = INITIALIZR_METADATA_URL) -> None:
        self.metadata_url = metadata_url

    def fetch(self) -> dict[str, Any]:
        try:
            with urlopen(self.metadata_url, timeout=15) as response:
                return json.load(response)
        except Exception as exc:  # noqa: BLE001
            raise MetadataFetchError(f"No se pudo obtener metadata oficial: {exc}") from exc

    def dependencies(self) -> list[DependencyOption]:
        metadata = self.fetch()
        raw_groups = metadata.get("dependencies", {}).get("values", [])
        results: list[DependencyOption] = []
        for group in raw_groups:
            for raw_dep in group.get("values", []):
                results.append(
                    DependencyOption(
                        id=str(raw_dep.get("id", "")).strip(),
                        name=str(raw_dep.get("name", "")).strip(),
                        description=str(raw_dep.get("description", "")).strip(),
                    )
                )
        return [dep for dep in results if dep.id]


class DependencyResolver:
    def __init__(self, metadata_client: InitializrMetadataClient) -> None:
        self.metadata_client = metadata_client

    def search(self, query: str) -> dict[str, Any]:
        query_normalized = query.strip().lower()
        try:
            available_dependencies = self.metadata_client.dependencies()
        except MetadataFetchError as exc:
            return {"status": "metadata_unavailable", "error": str(exc), "matches": []}

        matches = [
            dep
            for dep in available_dependencies
            if query_normalized in dep.id.lower()
            or query_normalized in dep.name.lower()
            or query_normalized in dep.description.lower()
        ]

        exact = [dep for dep in matches if dep.id.lower() == query_normalized]
        if exact:
            return {"status": "resolved", "matches": [self._as_dict(exact[0])]}

        if query_normalized in GENERIC_TERMS and len(matches) > 1:
            options = [self._as_dict(item) for item in matches[:5]]
            names = ", ".join(option["name"] for option in options)
            return {
                "status": "clarification_required",
                "query": query,
                "question": f"Tu solicitud es ambigua. ¿Cuál prefieres: {names}?",
                "options": options,
            }

        return {
            "status": "resolved" if matches else "not_found",
            "matches": [self._as_dict(item) for item in matches],
        }

    @staticmethod
    def _as_dict(dep: DependencyOption) -> dict[str, str]:
        return {"id": dep.id, "name": dep.name, "description": dep.description}


def detect_spring_boot_version(project_root: Path) -> str | None:
    pom_path = project_root / "pom.xml"
    if pom_path.exists():
        return _detect_boot_version_from_pom(pom_path)

    gradle_path = project_root / "build.gradle"
    if gradle_path.exists():
        return _detect_boot_version_from_gradle(gradle_path)

    gradle_kts_path = project_root / "build.gradle.kts"
    if gradle_kts_path.exists():
        return _detect_boot_version_from_gradle(gradle_kts_path)

    return None


def verify_bom_compatibility(boot_version: str | None, dependency_id: str) -> dict[str, Any]:
    bom_key = _match_external_bom_key(dependency_id)
    if not bom_key:
        return {
            "compatible": True,
            "reason": "La dependencia no requiere BOM externo conocido.",
            "requires_external_bom": False,
        }

    bom = EXTERNAL_BOMS[bom_key]
    if not boot_version:
        return {
            "compatible": False,
            "reason": "No se pudo detectar la versión de Spring Boot.",
            "requires_external_bom": True,
            "bom": {
                "groupId": bom.group_id,
                "artifactId": bom.artifact_id,
                "version": bom.version,
            },
        }

    requires_external_bom = _version_lt(boot_version, bom.min_boot_without_bom)
    return {
        "compatible": True,
        "reason": "Se requiere BOM externo para esta versión de Spring Boot."
        if requires_external_bom
        else "La versión de Spring Boot gestiona esta familia de dependencias.",
        "requires_external_bom": requires_external_bom,
        "bom": {
            "groupId": bom.group_id,
            "artifactId": bom.artifact_id,
            "version": bom.version,
        },
    }


def install_dependency_in_pom(
    pom_path: Path,
    group_id: str,
    artifact_id: str,
    version: str | None,
    bom: dict[str, str] | None,
) -> bool:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    tree = ET.parse(pom_path, parser=parser)
    root = tree.getroot()

    ns_uri = ""
    if root.tag.startswith("{"):
        ns_uri = root.tag.split("}", 1)[0][1:]

    def q(tag: str) -> str:
        return f"{{{ns_uri}}}{tag}" if ns_uri else tag

    dependencies = root.find(q("dependencies"))
    if dependencies is None:
        dependencies = ET.SubElement(root, q("dependencies"))

    for dep in dependencies.findall(q("dependency")):
        existing_group = (dep.findtext(q("groupId")) or "").strip()
        existing_artifact = (dep.findtext(q("artifactId")) or "").strip()
        if existing_group == group_id and existing_artifact == artifact_id:
            return False

    if bom:
        _ensure_bom_import(root, q, bom)

    dep_el = ET.SubElement(dependencies, q("dependency"))
    ET.SubElement(dep_el, q("groupId")).text = group_id
    ET.SubElement(dep_el, q("artifactId")).text = artifact_id
    if version and not bom:
        ET.SubElement(dep_el, q("version")).text = version

    _indent_xml(root)
    tree.write(pom_path, encoding="utf-8", xml_declaration=True)
    return True


def install_dependency_in_gradle(
    gradle_path: Path,
    dependency_notation: str,
) -> bool:
    content = gradle_path.read_text(encoding="utf-8")
    if dependency_notation in content:
        return False

    match = re.search(r"dependencies\s*\{", content)
    if not match:
        raise ValueError("No se encontró bloque dependencies { ... }")

    block_start = match.end()
    line = f"\n    implementation({dependency_notation})"
    updated = content[:block_start] + line + content[block_start:]
    gradle_path.write_text(updated, encoding="utf-8")
    return True


def run_dependency_verification(project_root: Path) -> dict[str, Any]:
    if (project_root / "mvnw").exists():
        command = ["./mvnw", "dependency:resolve"]
    elif (project_root / "gradlew").exists():
        command = ["./gradlew", "build", "--refresh-dependencies"]
    else:
        return {
            "success": False,
            "exit_code": 127,
            "command": None,
            "error": "No se encontró wrapper de Maven o Gradle.",
        }

    proc = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=180,
    )

    combined_output = _clean_console_output(proc.stderr or proc.stdout)
    result = {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "command": " ".join(command),
        "output": combined_output,
    }
    if proc.returncode != 0:
        result["error"] = _summarize_error(combined_output)
    return result


class SpringPackageMCPServer:
    def __init__(self, project_root: Path, metadata_client: InitializrMetadataClient | None = None) -> None:
        self.project_root = project_root
        self.metadata_client = metadata_client or InitializrMetadataClient()
        self.resolver = DependencyResolver(self.metadata_client)

    def search_dependency(self, query: str) -> dict[str, Any]:
        return self.resolver.search(query)

    def verify_bom_compatibility(self, dependency_id: str) -> dict[str, Any]:
        boot_version = detect_spring_boot_version(self.project_root)
        result = verify_bom_compatibility(boot_version, dependency_id)
        result["boot_version"] = boot_version
        return result

    def install_dependency(self, dependency_id: str) -> dict[str, Any]:
        resolution = self.search_dependency(dependency_id)
        if resolution["status"] != "resolved" or not resolution["matches"]:
            return resolution

        selected = resolution["matches"][0]
        group_id, artifact_id = _split_dependency_id(selected["id"])
        bom_result = self.verify_bom_compatibility(selected["id"])
        bom = bom_result.get("bom") if bom_result.get("requires_external_bom") else None

        pom_path = self.project_root / "pom.xml"
        gradle_path = self.project_root / "build.gradle"
        gradle_kts_path = self.project_root / "build.gradle.kts"

        if pom_path.exists():
            changed = install_dependency_in_pom(pom_path, group_id, artifact_id, None, bom)
            target = str(pom_path)
        elif gradle_path.exists():
            notation = f'"{group_id}:{artifact_id}"'
            changed = install_dependency_in_gradle(gradle_path, notation)
            target = str(gradle_path)
        elif gradle_kts_path.exists():
            notation = f'"{group_id}:{artifact_id}"'
            changed = install_dependency_in_gradle(gradle_kts_path, notation)
            target = str(gradle_kts_path)
        else:
            return {
                "status": "error",
                "error": "No se encontró pom.xml ni build.gradle/build.gradle.kts",
            }

        verification = run_dependency_verification(self.project_root)
        return {
            "status": "updated" if changed else "unchanged",
            "target_file": target,
            "dependency": selected,
            "verification": verification,
            "bom": bom_result,
        }


def _match_external_bom_key(dependency_id: str) -> str | None:
    lowered = dependency_id.lower()
    for key in EXTERNAL_BOMS:
        if key in lowered:
            return key
    return None


def _split_dependency_id(dependency_id: str) -> tuple[str, str]:
    parts = dependency_id.split(":", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    if dependency_id.startswith("spring-"):
        return "org.springframework.boot", f"spring-boot-starter-{dependency_id.replace('spring-', '', 1)}"
    return "org.springframework.boot", dependency_id


def _detect_boot_version_from_pom(pom_path: Path) -> str | None:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.parse(pom_path, parser=parser).getroot()

    ns_uri = ""
    if root.tag.startswith("{"):
        ns_uri = root.tag.split("}", 1)[0][1:]

    def q(tag: str) -> str:
        return f"{{{ns_uri}}}{tag}" if ns_uri else tag

    parent = root.find(q("parent"))
    if parent is not None:
        artifact = (parent.findtext(q("artifactId")) or "").strip()
        if artifact == "spring-boot-starter-parent":
            version = (parent.findtext(q("version")) or "").strip()
            if version:
                return version

    props = root.find(q("properties"))
    if props is not None:
        version = (props.findtext(q("spring-boot.version")) or "").strip()
        if version:
            return version

    return None


def _detect_boot_version_from_gradle(gradle_path: Path) -> str | None:
    content = gradle_path.read_text(encoding="utf-8")
    match = re.search(r'org\.springframework\.boot["\']\)?\s*version\s*["\']([^"\']+)["\']', content)
    if match:
        return match.group(1)
    return None


def _version_lt(left: str, right: str) -> bool:
    def parse(version: str) -> tuple[int, int, int]:
        cleaned = re.split(r"[-+]", version, maxsplit=1)[0]
        nums = [int(item) for item in cleaned.split(".") if item.isdigit()]
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums[:3])

    return parse(left) < parse(right)


def _ensure_bom_import(root: ET.Element, q: Any, bom: dict[str, str]) -> None:
    dep_mgmt = root.find(q("dependencyManagement"))
    if dep_mgmt is None:
        dep_mgmt = ET.SubElement(root, q("dependencyManagement"))
    deps = dep_mgmt.find(q("dependencies"))
    if deps is None:
        deps = ET.SubElement(dep_mgmt, q("dependencies"))

    for dep in deps.findall(q("dependency")):
        g = (dep.findtext(q("groupId")) or "").strip()
        a = (dep.findtext(q("artifactId")) or "").strip()
        if g == bom["groupId"] and a == bom["artifactId"]:
            return

    dep = ET.SubElement(deps, q("dependency"))
    ET.SubElement(dep, q("groupId")).text = bom["groupId"]
    ET.SubElement(dep, q("artifactId")).text = bom["artifactId"]
    ET.SubElement(dep, q("version")).text = bom["version"]
    ET.SubElement(dep, q("type")).text = "pom"
    ET.SubElement(dep, q("scope")).text = "import"


def _indent_xml(elem: ET.Element, level: int = 0) -> None:
    indent = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        for child in elem:
            _indent_xml(child, level + 1)
        if not elem[-1].tail or not elem[-1].tail.strip():
            elem[-1].tail = indent
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = indent


def _clean_console_output(text: str) -> str:
    ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    return ansi_escape.sub("", text).strip()


def _summarize_error(output: str, max_lines: int = 15) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def load_server_from_cwd() -> SpringPackageMCPServer:
    return SpringPackageMCPServer(Path(os.getcwd()))


class MetadataFetchError(RuntimeError):
    pass
