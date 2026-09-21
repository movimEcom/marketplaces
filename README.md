# marketplaces-mcp

Servidores MCP (Model Context Protocol) para conectar agentes de IA a APIs de
marketplaces. Actualmente incluye un servidor completo de **operaciones de
vendedor en Mercado Libre México (sitio `MLM`)**.

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

## Estructura

```
src/marketplaces_mcp/meli/
  config.py     # variables de entorno (client id/secret, site, ruta de tokens)
  auth.py       # OAuth2 Authorization Code + PKCE, refresh y almacenamiento de tokens
  authorize.py  # script interactivo de login (meli-mx-authorize)
  client.py     # wrapper HTTP sobre la API REST de Mercado Libre
  server.py     # servidor MCP (FastMCP) que expone las tools (meli-mx-mcp)
```

## Notas de seguridad

- El `Client Secret` y los tokens nunca deben commitearse; `.gitignore` ya
  excluye `.env` y cualquier `*meli_tokens.json`.
- El `refresh_token` sirve para operar indefinidamente en nombre de la cuenta
  autorizada: trátalo como una credencial sensible.
- Este servidor opera únicamente sobre la cuenta que completó el login; para
  conectar otra cuenta vendedora, corre `meli-mx-authorize` de nuevo apuntando
  a un `MELI_TOKEN_PATH` distinto.
