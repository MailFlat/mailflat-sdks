# MailFlat — MCP server

Native [Model Context Protocol](https://modelcontextprotocol.io) server for
[MailFlat](https://mailflat.net). Gives Claude Desktop, Cursor, or any MCP client
**real inbox tooling**: create inboxes, wait for OTP codes, send DKIM-signed mail, and
clean up when the task is done. Each address is permanent and stays until you delete it;
only the messages inside expire, on a retention window you choose. Built on the [`mailflat`](https://pypi.org/project/mailflat/) Python SDK.

## Run

```bash
# zero-install, isolated (recommended)
MAILFLAT_API_KEY=mf_live_… uvx mailflat-mcp

# or install it
pipx install mailflat-mcp
MAILFLAT_API_KEY=mf_live_… mailflat-mcp
```

## Claude Desktop / Cursor config

Add to `claude_desktop_config.json` (or your client's MCP config):

```json
{
  "mcpServers": {
    "mailflat": {
      "command": "uvx",
      "args": ["mailflat-mcp"],
      "env": { "MAILFLAT_API_KEY": "mf_live_..." }
    }
  }
}
```

Get your API key from the [MailFlat dashboard](https://mailflat.net) → Agents.

## Tools (14)

| Tool | What it does |
|---|---|
| `create_inbox(prefix?, label?, retention_hours?)` | Open an inbox; `retention_hours` capped by your plan |
| `list_inboxes()` | All inboxes this key can see |
| `read_messages(address, direction="in")` | Read messages; received mail by default (`out` / `all` for the rest) |
| `wait_for_otp(address, timeout=30)` | Poll until an OTP arrives, then return it |
| `wait_for_message(address, timeout=30)` | Poll until a new message arrives; ignores mail you sent |
| `send_email(address, to, subject?, body?, html?)` | Send a DKIM-signed mail from the inbox |
| `reply(address, message_id, body?, html?)` | Answer a message **in the same conversation** (threading headers filled in) |
| `wait_until_sent(address, message_id, timeout=60)` | Did that mail actually go out? A timeout means still queued, not lost: do not send again |
| `mark_read(address, message_id)` | Mark one message read so later polls skip it |
| `burn_inbox(address)` | Delete every message but KEEP the address |
| `delete_inbox(address)` | Delete the inbox and its messages |
| `delete_message(address, message_id)` | Delete one message; the inbox itself stays |
| `list_calendar_events(address, include_cancelled=False)` | Meetings from calendar invitations the inbox received, with this inbox's answer (`my_status`) |
| `rsvp_to_invite(address, event_id, response, comment?)` | Accept, decline or tentatively accept; the organizer's calendar shows the answer |

> Reads return **received** mail by default. Without that, sending to a peer and then
> waiting for the reply would immediately match your own outgoing message.

## Configuration

- `MAILFLAT_API_KEY` — your account API key (required).
- `MAILFLAT_API_URL` — override the API base (default `https://mailflat.net`; for self-hosted / BYOD).

## Registry

mcp-name: io.github.MailFlat/mailflat-mcp

This line is the ownership marker the official MCP Registry looks for in a PyPI package's
README. It proves the same owner controls both the GitHub namespace and the published
package, so the registry entry cannot be claimed by someone else. Keep it in sync with the
`name` field in `server.json`; a test asserts the two match.

## License

MIT
