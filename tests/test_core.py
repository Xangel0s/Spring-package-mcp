from __future__ import annotations

from pathlib import Path

from spring_package_mcp.core import (
    DependencyOption,
    DependencyResolver,
    InitializrMetadataClient,
    MetadataFetchError,
    SpringPackageMCPServer,
    install_dependency_in_gradle,
    install_dependency_in_pom,
    verify_bom_compatibility,
)


class FakeMetadataClient(InitializrMetadataClient):
    def dependencies(self) -> list[DependencyOption]:
        return [
            DependencyOption(
                id="org.springframework.boot:spring-boot-starter-data-jpa",
                name="Spring Data JPA",
                description="SQL relational databases",
            ),
            DependencyOption(
                id="org.springframework.boot:spring-boot-starter-data-mongodb",
                name="Spring Data MongoDB",
                description="NoSQL database",
            ),
            DependencyOption(
                id="org.springframework.ai:spring-ai-openai-spring-boot-starter",
                name="Spring AI OpenAI",
                description="AI integration",
            ),
        ]


class UnavailableMetadataClient(InitializrMetadataClient):
    def dependencies(self) -> list[DependencyOption]:
        raise MetadataFetchError("offline")


def test_ambiguity_requires_clarification() -> None:
    resolver = DependencyResolver(FakeMetadataClient())

    result = resolver.search("database")

    assert result["status"] == "clarification_required"
    assert len(result["options"]) >= 2


def test_search_handles_metadata_outage() -> None:
    resolver = DependencyResolver(UnavailableMetadataClient())

    result = resolver.search("database")

    assert result["status"] == "metadata_unavailable"


def test_verify_bom_requires_external_for_old_boot() -> None:
    result = verify_bom_compatibility(
        "3.3.1", "org.springframework.ai:spring-ai-openai-spring-boot-starter"
    )

    assert result["requires_external_bom"] is True
    assert result["bom"]["artifactId"] == "spring-ai-bom"


def test_install_dependency_in_pom_adds_dependency_and_bom(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <modelVersion>4.0.0</modelVersion>
  <dependencies></dependencies>
</project>
""",
        encoding="utf-8",
    )

    changed = install_dependency_in_pom(
        pom,
        "org.springframework.ai",
        "spring-ai-openai-spring-boot-starter",
        None,
        {
            "groupId": "org.springframework.ai",
            "artifactId": "spring-ai-bom",
            "version": "1.0.0",
        },
    )

    output = pom.read_text(encoding="utf-8")
    assert changed is True
    assert "spring-ai-openai-spring-boot-starter" in output
    assert "dependencyManagement" in output
    assert "spring-ai-bom" in output


def test_install_dependency_in_gradle_adds_implementation(tmp_path: Path) -> None:
    gradle = tmp_path / "build.gradle"
    gradle.write_text(
        """plugins { id 'java' }

dependencies {
}
""",
        encoding="utf-8",
    )

    changed = install_dependency_in_gradle(gradle, '"org.springframework.boot:spring-boot-starter-web"')

    assert changed is True
    assert "implementation(\"org.springframework.boot:spring-boot-starter-web\")" in gradle.read_text(
        encoding="utf-8"
    )


def test_server_install_dependency_returns_error_without_build_file(tmp_path: Path) -> None:
    server = SpringPackageMCPServer(tmp_path, metadata_client=FakeMetadataClient())

    result = server.install_dependency("org.springframework.boot:spring-boot-starter-data-jpa")

    assert result["status"] == "error"
