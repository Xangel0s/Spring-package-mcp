# Spring-package-mcp

Servidor MCP inteligente para proyectos Spring Boot locales.

## Herramientas expuestas

- `search_dependency`: consulta `https://start.spring.io/metadata/client` y detecta ambigüedad para términos genéricos.
- `install_dependency`: inserta dependencias en `pom.xml` (AST XML) o `build.gradle` / `build.gradle.kts` (bloque `dependencies`).
- `verify_bom_compatibility`: detecta versión de Spring Boot local y decide si requiere BOM externo (por ejemplo `spring-ai-bom`).

## Ejecución

```bash
python -m spring_package_mcp
```

Entrada por línea JSON:

```json
{"tool":"search_dependency","arguments":{"query":"database"}}
```

## Integración con Antigravity

Configurar `mcpServers` con este comando:

```json
{
  "mcpServers": {
    "spring-package": {
      "command": "python",
      "args": ["-m", "spring_package_mcp"],
      "cwd": "/ruta/a/proyecto/spring"
    }
  }
}
```
