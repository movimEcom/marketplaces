# marketplaces-mcp

Servidores MCP (Model Context Protocol) para conectar agentes de IA a APIs de
marketplaces. Actualmente incluye un servidor completo de **operaciones de
vendedor en Mercado Libre México (sitio `MLM`)** y un conector de solo
lectura a **Alpha ERP** (ver [Alpha ERP](#alpha-erp-solo-lectura)).

## 1. Crear la aplicación en Mercado Libre

1. Entra a https://developers.mercadolibre.com.mx/ con la cuenta de vendedor
   que quieres conectar y crea una nueva aplicación ("Mis aplicaciones" →
   "Crear aplicación").
2. Como **Redirect URI** usa exactamente la misma URL que vas a poner en
   `MELI_REDIRECT_URI` (por defecto `http://localhost:8765/callback`). Debe
   coincidir carácter por carácter.
3. Guarda el **Client ID** y el **Client Secret** que te genera.

No compartas esas credenciales en el chat ni las subas al repositorio.

## 2. Configurar el proyecto

```bash
cp .env.example .env
# edita .env y agrega MELI_CLIENT_ID / MELI_CLIENT_SECRET

uv sync
```

## 3. Autenticarte (una sola vez por cuenta vendedora)

```bash
uv run meli-mx-authorize
```

Esto abre el navegador para que inicies sesión y autorices la app. Al
terminar, guarda el `access_token`/`refresh_token` localmente en la ruta de
`MELI_TOKEN_PATH` (por defecto `~/.config/marketplaces-mcp/meli_tokens.json`,
con permisos `600`). El servidor MCP renueva el `access_token`
automáticamente con el `refresh_token` cuando expira, así que este paso no
hay que repetirlo salvo que revoques el permiso desde Mercado Libre.

## 4. Levantar el servidor MCP

```bash
uv run meli-mx-mcp
```

Usa transporte `stdio`, por lo que normalmente no lo ejecutas a mano sino que
lo registras en tu cliente MCP (Claude Code, Claude Desktop, etc.):

```json
{
  "mcpServers": {
    "mercadolibre-mexico": {
      "command": "uv",
      "args": ["--directory", "/ruta/a/marketplaces", "run", "meli-mx-mcp"]
    }
  }
}
```

En Claude Code puedes agregarlo con:

```bash
claude mcp add mercadolibre-mexico -- uv --directory /ruta/a/marketplaces run meli-mx-mcp
```

## Herramientas disponibles

**Catálogo público**
- `meli_search_products(query, limit, offset)`
- `meli_get_item(item_id)`
- `meli_get_categories()`
- `meli_get_category_attributes(category_id)`

**Publicaciones propias**
- `meli_list_my_items(status, limit, offset)`
- `meli_create_item(item)`
- `meli_update_item(item_id, changes)`
- `meli_update_price(item_id, price)`
- `meli_update_stock(item_id, quantity)`
- `meli_set_item_status(item_id, status)`

**Órdenes**
- `meli_list_orders(status, limit, offset)`
- `meli_get_order(order_id)`

**Preguntas y mensajería**
- `meli_list_questions(status, limit)`
- `meli_answer_question(question_id, text)`
- `meli_list_order_messages(order_id)`
- `meli_send_order_message(order_id, text)`

**Reputación**
- `meli_get_my_reputation()`
- `meli_get_user(user_id)`

**Calidad de publicaciones**

Mercado Libre mide la calidad distinto según el tipo de publicación, así que
el reporte separa ambos:
- **Publicaciones regulares**: usan `/item/{id}/performance` (score 0-100,
  el mismo que ves en tu panel de vendedor), bucketed en Excelente 80-100 /
  Bueno 60-79 / Mejorable 40-59 / Crítico 0-39, con los "objetivos
  pendientes" de cada una.
- **Publicaciones de catálogo** (con `user_product_id`, ficha compartida con
  otros vendedores del mismo producto): no tienen score 0-100 público — se
  reporta si su ficha está `COMPLETE` o no, vía `/products/{catalog_product_id}`.

Tools:
- `meli_get_listing_quality_summary(status)` — resumen de ambos tipos, con
  las publicaciones que necesitan atención en cada uno.
- `meli_generate_quality_dashboard(output_path, status)` — el mismo resumen
  como página HTML con dos secciones.

## Dashboard de calidad sin Claude Code

Si solo quieres el reporte HTML (sin pasar por un cliente MCP), hay un
comando independiente:

```bash
uv run meli-mx-quality-report                 # genera quality_report.html
uv run meli-mx-quality-report reporte.html --open   # nombre custom y lo abre en el navegador
uv run meli-mx-quality-report --all           # incluye pausadas/cerradas, no solo activas
```

Es un snapshot bajo demanda: cada corrida vuelve a consultar Mercado Libre y
regenera el archivo con los datos actuales (no guarda histórico entre
corridas).

## Alpha ERP (solo lectura)

Alpha ERP (Castelec) no usa SQL Server: guarda sus datos en archivos **.DBF**
(xBase/FoxPro) dentro de una carpeta del servidor. Este conector lee esos
archivos directamente, **solo en modo lectura** (nunca escribe ni bloquea las
tablas), así que puede correr mientras Alpha está en uso.

### ¿Dónde correrlo si hoy entras por Remote Desktop?

El conector necesita ver la carpeta de datos de Alpha como archivos. Elige una:

1. **En el mismo servidor (lo más simple).** Desde tu sesión de Remote Desktop
   instala `uv` y este repo en el servidor y apunta `ALPHA_DATA_DIR` a la ruta
   local, p. ej. `C:\Alpha\Datos`. El cliente MCP (Claude Desktop / Claude
   Code) también corre ahí.
2. **Desde tu PC por carpeta compartida.** Si tu PC está en la misma red (o
   VPN) que el servidor, comparte la carpeta de datos en Windows (clic derecho →
   Propiedades → Compartir, con permiso de **solo lectura**) y usa
   `ALPHA_DATA_DIR=\\SERVIDOR\Alpha\Datos` (en Mac/Linux, móntala primero con
   SMB y usa la ruta del montaje).
3. **Desde fuera de la oficina.** No abras el puerto SMB (445) a internet. Usa
   una VPN (p. ej. Tailscale o la VPN del firewall) y luego la opción 2.

Para encontrar la carpeta de datos: en el servidor busca dónde están los
archivos `.DBF` (suele haber una subcarpeta por empresa). El conector las
recorre todas y nombra cada tabla por su ruta relativa, p. ej.
`emp01/clientes`.

### Instalación rápida en Windows

Desde la carpeta del repo, en el servidor (o en la PC que tiene la unidad
`Y:` mapeada):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-alpha-windows.ps1
```

Usa `\\endocat2.dyndns.org\vsai\Empresas\ENDOCAT` por defecto y solo sus tablas
vigentes (Alpha guarda respaldos completos en subcarpetas por fecha como
`20260723\`; `-ScanDepth 1` los incluye);
pasa `-DataDir "X:\otra"` para otra ruta.
Instala `uv`, convierte la unidad mapeada a su ruta `\\SERVIDOR\...` (Claude
Desktop no siempre ve las unidades mapeadas), escribe `.env`, corre
`alpha-erp-check` y registra `alpha-erp` en Claude Desktop.

### Configurar y probar a mano

```bash
# en .env
ALPHA_DATA_DIR=C:\Alpha\Datos

uv sync
uv run alpha-erp-check            # lista las tablas que encuentra
uv run alpha-erp-check clientes   # además muestra campos y 3 registros
```

Si ves acentos o `ñ` mal, prueba `ALPHA_DBF_ENCODING=cp850` (o `cp1252`).
Solo se buscan `.DBF` en la carpeta y un nivel de subcarpetas
(`ALPHA_SCAN_DEPTH=1`), para no recorrer miles de XML/PDF por la VPN; súbelo
si faltan tablas.

### Registrar el servidor MCP

```bash
claude mcp add alpha-erp -- uv --directory /ruta/a/marketplaces run alpha-erp-mcp
```

Tools:
- `alpha_list_tables(name_contains, refresh)` — tablas .DBF con tamaño y fecha.
- `alpha_describe_table(table)` — campos, tipos y número de registros.
- `alpha_query_table(table, fields, equals, contains, limit, offset)` — lee
  filas con filtros simples y paginación.
- `alpha_search(text, table_contains, limit)` — busca un texto (cliente, SKU,
  RFC, folio) en todas las tablas; útil para descubrir el esquema.

## Estructura

```
src/marketplaces_mcp/meli/
  config.py     # variables de entorno (client id/secret, site, ruta de tokens)
  auth.py       # OAuth2 Authorization Code + PKCE, refresh y almacenamiento de tokens
  authorize.py  # script interactivo de login (meli-mx-authorize)
  client.py     # wrapper HTTP sobre la API REST de Mercado Libre
  quality.py    # cálculo de bandas de calidad + render del HTML del dashboard
  report.py     # CLI standalone del dashboard (meli-mx-quality-report)
  server.py     # servidor MCP (FastMCP) que expone las tools (meli-mx-mcp)

src/marketplaces_mcp/alpha/
  config.py     # ALPHA_DATA_DIR, encoding, límite de filas
  dbf.py        # lectura (solo lectura) de las tablas .DBF de Alpha ERP
  check.py      # prueba de conexión (alpha-erp-check)
  server.py     # servidor MCP con las tools alpha_* (alpha-erp-mcp)
```

## Notas de seguridad

- El `Client Secret` y los tokens nunca deben commitearse; `.gitignore` ya
  excluye `.env` y cualquier `*meli_tokens.json`.
- El `refresh_token` sirve para operar indefinidamente en nombre de la cuenta
  autorizada: trátalo como una credencial sensible.
- Este servidor opera únicamente sobre la cuenta que completó el login; para
  conectar otra cuenta vendedora, corre `meli-mx-authorize` de nuevo apuntando
  a un `MELI_TOKEN_PATH` distinto.
