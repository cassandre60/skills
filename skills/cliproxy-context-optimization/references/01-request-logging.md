# Request logging: the two switches and where the files go

This reference exists because the usual failure is not a broken proxy. It is a
config that looks enabled, a log directory that looks active, and a setting that
does not capture bodies.

## The switches are independent

| Key | What it does | What it does not do |
| --- | --- | --- |
| `request-log` | Writes proxy request and response bodies to per-request captures under the logs directory. Management requests are excluded. | Does not write ordinary application logs. |
| `logging-to-file` | Writes application logs to rotating files instead of stdout. | Does not write request or response bodies. |
| `debug` | Adds more application logging. | Does not capture bodies. |

Enabling `logging-to-file` and then searching the files for a prompt is the
common wrong turn. The prompt only appears when `request-log` is on.

## The layout moved: read the active file

The keys are the same, but their nesting differs by version. CLIProxyAPI 8.0.27
uses:

```yaml
observability:
    logs:
        request-log: false
        debug: false
        logging-to-file: false
        logs-max-total-size-mb: 0
        error-logs-max-files: 10
```

Older examples, panels, and migration notes may show `request-log` at the top
level or under a different parent. Do not edit a remembered location and assume
it took effect. Find the uncommented keys in the actual file:

```sh
grep -nE '^\s*(request-log|logging-to-file|debug|auth-dir):' config.yaml
```

To enable bodies on 8.0.27, set:

```yaml
observability:
    logs:
        request-log: true
```

Then restart the proxy process. A config change in a file is not live until the
process reloads it. Old captures remain; a new capture should appear only after
the restart and the next request.

If the file has both a legacy top-level key and the nested v8 key, do not assume
which one the running binary honours. The v8 layout documentation says the v8
value wins when both spellings are present. Treat the file the process was
started with as the only relevant one, and verify with the finder after restart.
The finder's `--json` report includes a `sources` object with the dotted path and
line number of each value, so you can see whether `false` came from
`observability.logs.request-log` or from a legacy key.

## Where the files go: resolve `auth-dir`

The logs directory is:

```
<auth-dir>/logs
```

On 8.0.27, `auth-dir` is under `oauth`:

```yaml
oauth:
    auth-dir: "/home/you/.cli-proxy-api"
```

Older layouts may use a different parent or a top-level key. Find it rather than
assuming:

```sh
grep -nE '^\s*auth-dir:' config.yaml
```

If `auth-dir` is relative, resolve it against the config file's directory, not
against the shell's current directory. The finder does this.

Common defaults worth checking when the config is unavailable:

- `~/.cli-proxy-api/logs`
- `~/.config/cli-proxy-api/logs`
- `/var/log/cli-proxy-api`

Do not treat those as authoritative. They are fallbacks for discovery, not a
substitute for reading `auth-dir`.

## What the files prove

| File | Meaning |
| --- | --- |
| `v1-*.log` | A successful request capture exists. This is the file to render. |
| `error-*.log` | A failed request capture. It can be retained even when request logging is disabled, up to `error-logs-max-files`. |
| Other `.log` files | Application logs. They do not contain request bodies. |
| `request-log-parts-*` directories | Possible storage for large split bodies. The finder reports them; the renderer expects a complete unsplit `REQUEST BODY`. |

A directory can contain ten error logs and zero request captures. That is not a
contradiction; it is the expected shape when `request-log` is off.

## The management endpoint is not a body dump

`GET /v0/management/logs` returns application log lines. It does not return the
request and response bodies from a capture. It also requires the configured
management key. Use it to inspect the proxy's own logging, not to retrieve a
prompt.

Do not print or commit the management key. The finder reports the URL from the
configured port and nothing else.

## Enable, restart, prove

1. Read the active config file and confirm the parent of the log keys.
2. Set `request-log: true`. Leave `logging-to-file` alone unless application log
   files are also wanted.
3. Restart the proxy with that file.
4. Send one small request through the proxy.
5. Run the finder:

   ```sh
   python3 scripts/find_request_logs.py --config PATH/TO/config.yaml
   ```

6. Confirm a new `v1-*.log` appears with a current timestamp. The timestamp in
   the filename is the capture start, not the file's modification time.
7. Render that capture and confirm the prompt appears in the transcript.

If step 6 fails, check in this order: the process was actually restarted; the
config path is the one the process uses; `request-log` is uncommented and true;
`auth-dir` resolves to the directory being inspected; the request reached the
proxy rather than failing at the client.

## Retention and privacy

Request captures contain full prompts, system instructions, tool definitions,
and response text. They can also contain Authorization headers and upstream
credentials. Treat the logs directory as sensitive:

- Do not commit captures or rendered bundles to a repository.
- Redact headers and account identifiers before sharing a bundle.
- Use `logs-max-total-size-mb` to cap the total log directory size; `0` disables
  the cap. Use `error-logs-max-files` to cap retained error logs.
- Keep the management key and provider keys out of the transcript. The renderer
  does not add secrets, but it does preserve whatever the original headers
  contain.

## Prior setup on the originating machine

The machine that produced this skill had already completed the setup: request
logging was enabled after `logging-to-file` proved insufficient, application log
files were enabled separately, `auth-dir` was confirmed, and the management
endpoint was checked as application-logs-only. The captures used to develop the
renderer came from that setup.

That setup is recorded here so it is not re-explained or repeated blindly. On
this machine, verify the current state with the finder; do not assume the values
in this document are still active.
