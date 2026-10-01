# AgentKit End-to-End Live Verification Report

**Environment**: macOS (Darwin 24.6.0) / Docker Compose v2.33.1  
**Target Services**: `agentkit-api`, `agentkit-postgres` (PostgreSQL 16), `agentkit-redis` (Redis 7)  
**LLM Engine**: Google Gemini API via official \`google-genai\` SDK (`gemini-3.1-flash-lite` active flash model)  
**Date**: 2026-10-01  
**Verification Status**: **ALL CHECKS PASSED (100% LIVE VERIFIED)**

---

## 1. Live Infrastructure Health

### Command
```bash
curl -s http://localhost:8000/health
```

### Verbatim Output
```json
{"status":"healthy","database":"healthy","redis":"healthy","details":{}}
```

### Tool Discovery
```bash
curl -s -H "X-API-Key: ak_test_key_12345" http://localhost:8000/tools
```

### Verbatim Output
```json
{
  "tools": [
    {
      "name": "calculator",
      "description": "Evaluate a basic arithmetic expression safely using AST parsing.",
      "parameters": {
        "type": "object",
        "properties": {
          "expression": {
            "title": "Expression",
            "type": "string"
          }
        },
        "required": ["expression"]
      }
    },
    {
      "name": "read_file",
      "description": "Read the contents of a text file within the sandboxed directory.",
      "parameters": {
        "type": "object",
        "properties": {
          "file_path": {
            "title": "File Path",
            "type": "string"
          }
        },
        "required": ["file_path"]
      }
    },
    {
      "name": "http_fetch",
      "description": "Fetch the text content of a public web page via HTTP GET.",
      "parameters": {
        "type": "object",
        "properties": {
          "url": {
            "title": "Url",
            "type": "string"
          },
          "timeout_s": {
            "default": 10.0,
            "title": "Timeout S",
            "type": "number"
          },
          "max_chars": {
            "default": 100000,
            "title": "Max Chars",
            "type": "integer"
          }
        },
        "required": ["url"]
      }
    },
    {
      "name": "sql_readonly",
      "description": "Execute a read-only SQL query against the database and return results as text.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "title": "Query",
            "type": "string"
          }
        },
        "required": ["query"]
      }
    },
    {
      "name": "web_search",
      "description": "Search the web for real-time information, documentation, and external references.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "title": "Query",
            "type": "string"
          }
        },
        "required": ["query"]
      }
    }
  ]
}
```

---

## 2. Multi-Step Analytical Demo Run (`sql_readonly` + `calculator`)

### Request
```bash
curl -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ak_test_key_12345" \
  -d '{"goal": "What was the total revenue from Electronics category in 2024, and what would be the total if we applied an 8.5% sales tax?"}'
```

### Verbatim Run Response
```json
{
  "run_id": "f4661784-3472-4622-ad07-1185edadcf4b",
  "session_id": null,
  "goal": "What was the total revenue from Electronics category in 2024, and what would be the total if we applied an 8.5% sales tax?",
  "status": "succeeded",
  "answer": "The total revenue from the Electronics category in 2024 was $3,200.00. \n\nIf an 8.5% sales tax were applied to this amount, the total would be $3,472.00.",
  "failure_reason": null,
  "total_input_tokens": 5247,
  "total_output_tokens": 306,
  "created_at": "2026-10-01T15:35:07.524471Z",
  "finished_at": "2026-10-01T15:35:34.138056Z"
}
```

### Trace Retrieval
```bash
curl -s -H "X-API-Key: ak_test_key_12345" http://localhost:8000/runs/f4661784-3472-4622-ad07-1185edadcf4b/trace
```

### Verbatim Trace Output
```json
{
  "run_id": "f4661784-3472-4622-ad07-1185edadcf4b",
  "steps": [
    {
      "step_no": 1,
      "tool_name": "sql_readonly",
      "args": {
        "query": "SELECT name FROM sqlite_master WHERE type='table';"
      },
      "result": {
        "ok": false,
        "output": "",
        "error": "Error executing tool 'sql_readonly': (sqlalchemy.dialects.postgresql.asyncpg.ProgrammingError) relation \"sqlite_master\" does not exist\n[SQL: SELECT name FROM sqlite_master WHERE type='table']\n(Background on this error at: https://sqlalche.me/e/21/f405)",
        "truncated": false,
        "latency_ms": 21
      },
      "error": "Error executing tool 'sql_readonly': (sqlalchemy.dialects.postgresql.asyncpg.ProgrammingError) relation \"sqlite_master\" does not exist\n[SQL: SELECT name FROM sqlite_master WHERE type='table']\n(Background on this error at: https://sqlalche.me/e/21/f405)",
      "latency_ms": 21,
      "input_tokens": 451,
      "output_tokens": 26,
      "timestamp": "2026-10-01T15:35:09.999267Z"
    },
    {
      "step_no": 2,
      "tool_name": "sql_readonly",
      "args": {
        "query": "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';"
      },
      "result": {
        "ok": true,
        "output": "| table_name |\n| --- |\n| alembic_version |\n| products |\n| orders |\n| steps |\n| runs |",
        "error": null,
        "truncated": false,
        "latency_ms": 9
      },
      "error": null,
      "latency_ms": 9,
      "input_tokens": 564,
      "output_tokens": 33,
      "timestamp": "2026-10-01T15:35:12.490092Z"
    },
    {
      "step_no": 3,
      "tool_name": "sql_readonly",
      "args": {
        "query": "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'orders';"
      },
      "result": {
        "ok": true,
        "output": "| column_name | data_type |\n| --- | --- |\n| id | integer |\n| product_id | integer |\n| quantity | integer |\n| total_price | numeric |\n| order_date | date |\n| customer_name | character varying |",
        "error": null,
        "truncated": false,
        "latency_ms": 15
      },
      "error": null,
      "latency_ms": 15,
      "input_tokens": 642,
      "output_tokens": 37,
      "timestamp": "2026-10-01T15:35:15.409812Z"
    },
    {
      "step_no": 4,
      "tool_name": "sql_readonly",
      "args": {
        "query": "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'products';"
      },
      "result": {
        "ok": true,
        "output": "| column_name | data_type |\n| --- | --- |\n| id | integer |\n| price | numeric |\n| stock | integer |\n| name | character varying |\n| category | character varying |",
        "error": null,
        "truncated": false,
        "latency_ms": 11
      },
      "error": null,
      "latency_ms": 11,
      "input_tokens": 752,
      "output_tokens": 37,
      "timestamp": "2026-10-01T15:35:25.066776Z"
    },
    {
      "step_no": 5,
      "tool_name": "sql_readonly",
      "args": {
        "query": "SELECT SUM(o.total_price) \nFROM orders o\nJOIN products p ON o.product_id = p.id\nWHERE p.category = 'Electronics' \nAND o.order_date >= '2024-01-01' \nAND o.order_date <= '2024-12-31';"
      },
      "result": {
        "ok": true,
        "output": "| sum |\n| --- |\n| 3200.00 |",
        "error": null,
        "truncated": false,
        "latency_ms": 14
      },
      "error": null,
      "latency_ms": 14,
      "input_tokens": 849,
      "output_tokens": 94,
      "timestamp": "2026-10-01T15:35:28.905821Z"
    },
    {
      "step_no": 6,
      "tool_name": "calculator",
      "args": {
        "expression": "3200 * 1.085"
      },
      "result": {
        "ok": true,
        "output": "3472.0",
        "error": null,
        "truncated": false,
        "latency_ms": 3
      },
      "error": null,
      "latency_ms": 3,
      "input_tokens": 974,
      "output_tokens": 24,
      "timestamp": "2026-10-01T15:35:31.964681Z"
    }
  ]
}
```

### PostgreSQL Durable Database Verification
```bash
docker compose exec postgres psql -U postgres -d agentkit -c \
  "SELECT id, status, total_input_tokens, total_output_tokens, finished_at FROM runs WHERE id = 'f4661784-3472-4622-ad07-1185edadcf4b';"
```

```
                  id                  |  status   | total_input_tokens | total_output_tokens |          finished_at          
--------------------------------------+-----------+--------------------+---------------------+-------------------------------
 f4661784-3472-4622-ad07-1185edadcf4b | succeeded |               5247 |                 306 | 2026-10-01 15:35:34.138056+00
(1 row)
```

```bash
docker compose exec postgres psql -U postgres -d agentkit -c \
  "SELECT step_no, tool_name, latency_ms, input_tokens, output_tokens FROM steps WHERE run_id = 'f4661784-3472-4622-ad07-1185edadcf4b' ORDER BY step_no;"
```

```
 step_no |  tool_name   | latency_ms | input_tokens | output_tokens 
---------+--------------+------------+--------------+---------------
       1 | sql_readonly |         21 |          451 |            26
       2 | sql_readonly |          9 |          564 |            33
       3 | sql_readonly |         15 |          642 |            37
       4 | sql_readonly |         11 |          752 |            37
       5 | sql_readonly |         14 |          849 |            94
       6 | calculator   |          3 |          974 |            24
(6 rows)
```

---

## 3. Security & Operational Guardrails Verification

### Test 1: Unknown Tool Handling
**Request**:
```bash
curl -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ak_test_key_12345" \
  -d '{"goal": "Invoke tool quantum_teleport with argument distance=5. If it does not exist, explain that."}'
```
**Verbatim Output**:
```json
{
  "run_id": "bec2e876-7f30-4de4-8e7d-7e3ae6a9c1a4",
  "session_id": null,
  "goal": "Invoke tool quantum_teleport with argument distance=5. If it does not exist, explain that.",
  "status": "succeeded",
  "answer": "The tool `quantum_teleport` does not exist within my available toolset. Therefore, I cannot perform that action.",
  "failure_reason": null,
  "total_input_tokens": 948,
  "total_output_tokens": 45,
  "created_at": "2026-10-01T15:35:55.705107Z",
  "finished_at": "2026-10-01T15:36:27.791519Z"
}
```

---

### Test 2: Bad Tool Arguments Handling
**Request**:
```bash
curl -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ak_test_key_12345" \
  -d '{"goal": "Execute the calculator tool with expression: '\''abc + def'\'' and report what happens."}'
```
**Verbatim Output**:
```json
{
  "run_id": "c0392205-8182-46db-b4dd-e6a59be23ad5",
  "session_id": null,
  "goal": "Execute the calculator tool with expression: 'abc + def' and report what happens.",
  "status": "succeeded",
  "answer": "The calculator tool returned an error because the input `'abc + def'` contains non-numeric characters (\"abc\" and \"def\"), which are not valid in a mathematical expression. The tool reported: `ERROR: Error executing tool 'calculator': Invalid expression syntax: invalid syntax (<unknown>, line 1)`.",
  "failure_reason": null,
  "total_input_tokens": 918,
  "total_output_tokens": 78,
  "created_at": "2026-10-01T15:36:32.219291Z",
  "finished_at": "2026-10-01T15:36:42.290964Z"
}
```
**Trace Step Output**:
```json
{
  "step_no": 1,
  "tool_name": "calculator",
  "args": {"expression": "abc + def"},
  "result": {
    "ok": false,
    "output": "",
    "error": "Error executing tool 'calculator': Invalid expression syntax: invalid syntax (<unknown>, line 1)",
    "truncated": false,
    "latency_ms": 1
  }
}
```

---

### Test 3: Tool Exception Handling (Division by Zero)
**Request**:
```bash
curl -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ak_test_key_12345" \
  -d '{"goal": "Calculate 100 / 0 using the calculator tool and report the result."}'
```
**Verbatim Output**:
```json
{
  "run_id": "9ba6726e-5920-438c-98f0-370b53b94061",
  "session_id": null,
  "goal": "Calculate 100 / 0 using the calculator tool and report the result.",
  "status": "succeeded",
  "answer": "The calculation of 100 / 0 results in an error, as division by zero is mathematically undefined.",
  "failure_reason": null,
  "total_input_tokens": 911,
  "total_output_tokens": 42,
  "created_at": "2026-10-01T15:36:59.848917Z",
  "finished_at": "2026-10-01T15:37:07.017358Z"
}
```
**Trace Step Output**:
```json
{
  "step_no": 1,
  "tool_name": "calculator",
  "args": {"expression": "100 / 0"},
  "result": {
    "ok": false,
    "output": "",
    "error": "Error executing tool 'calculator': Division by zero",
    "truncated": false,
    "latency_ms": 5
  }
}
```

---

### Test 4: Max-Steps Exceeded Enforcement
**Request**:
```bash
curl -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ak_test_key_12345" \
  -d '{"goal": "Calculate in sequence: compute 1+1 with calculator, then in the next step compute 2+2, then 3+3, then 4+4, then 5+5, then 6+6, then 7+7, then 8+8, then 9+9, then 10+10, then 11+11, then 12+12. Only compute one per turn."}'
```
**Verbatim Output**:
```json
{
  "run_id": "d9868281-17d5-4969-a527-95dd16f43d15",
  "session_id": null,
  "goal": "Calculate in sequence: compute 1+1 with calculator, then in the next step compute 2+2, then 3+3, then 4+4, then 5+5, then 6+6, then 7+7, then 8+8, then 9+9, then 10+10, then 11+11, then 12+12. Only compute one per turn.",
  "status": "max_steps_exceeded",
  "answer": null,
  "failure_reason": "Exceeded max steps (10)",
  "total_input_tokens": 6405,
  "total_output_tokens": 162,
  "created_at": "2026-10-01T15:37:16.804156Z",
  "finished_at": "2026-10-01T15:37:47.024937Z"
}
```

---

### Test 5: Sliding-Window Rate Limiting (HTTP 429)
**Execution**:
35 rapid requests fired using Python `urllib` against `GET http://localhost:8000/tools` with `X-API-Key: ak_test_key_12345`.

**Verbatim Results**:
```
Total requests: 35
200 OK count: 26
429 Too Many Requests count: 9
Sample 429: code=429, Retry-After=2, body={"error":{"code":"RATE_LIMIT_EXCEEDED","message":"Rate limit exceeded. Try again in 2 seconds."}}
```

---

### Test 6: SQL Injection Defense (Non-SELECT Blocked)
**Request**:
```bash
curl -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ak_test_key_12345" \
  -d '{"goal": "Execute sql_readonly with query: '\''DROP TABLE products;'\'' and explain what occurred."}'
```
**Verbatim Output**:
```json
{
  "run_id": "36b1d5f9-d08d-4d54-8471-5c14218c36fd",
  "session_id": null,
  "goal": "Execute sql_readonly with query: 'DROP TABLE products;' and explain what occurred.",
  "status": "succeeded",
  "answer": "The attempt to execute the `DROP TABLE products;` command failed. The `sql_readonly` tool is configured to strictly enforce read-only operations, meaning it only permits the execution of `SELECT` statements. Consequently, any attempt to modify the database structure, such as dropping a table, is blocked by the system.",
  "failure_reason": null,
  "total_input_tokens": 918,
  "total_output_tokens": 84,
  "created_at": "2026-10-01T15:38:01.814925Z",
  "finished_at": "2026-10-01T15:38:05.072446Z"
}
```
**Trace Step Output**:
```json
{
  "step_no": 1,
  "tool_name": "sql_readonly",
  "args": {"query": "DROP TABLE products;"},
  "result": {
    "ok": false,
    "output": "",
    "error": "Error executing tool 'sql_readonly': Only SELECT statements are permitted.",
    "truncated": false,
    "latency_ms": 0
  }
}
```

---

### Test 7: Path Traversal Sandboxing Defense
**Request**:
```bash
curl -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ak_admin_key_67890" \
  -d '{"goal": "Read the file at path '\''../../etc/passwd'\'' using read_file tool and report what happens."}'
```
**Verbatim Output**:
```json
{
  "run_id": "090c2cf1-0c94-492d-aace-48af95dc50bc",
  "session_id": null,
  "goal": "Read the file at path '../../etc/passwd' using read_file tool and report what happens.",
  "status": "succeeded",
  "answer": "The attempt to read the file at `../../etc/passwd` was unsuccessful. The system returned an error stating \"Access denied: path '../../etc/passwd' resolves outside allowed base directory,\" indicating that security restrictions prevent access to files outside of the designated environment.",
  "failure_reason": null,
  "total_input_tokens": 935,
  "total_output_tokens": 73,
  "created_at": "2026-10-01T15:38:26.995803Z",
  "finished_at": "2026-10-01T15:38:37.025897Z"
}
```
**Trace Step Output**:
```json
{
  "step_no": 1,
  "tool_name": "read_file",
  "args": {"file_path": "../../etc/passwd"},
  "result": {
    "ok": false,
    "output": "",
    "error": "Error executing tool 'read_file': Access denied: path '../../etc/passwd' resolves outside allowed base directory.",
    "truncated": false,
    "latency_ms": 2
  }
}
```

---

## 4. Verification Summary Matrix

| Verification Check | Target Component | Result | Verbatim Proof Recorded |
|:---|:---|:---:|:---:|
| **Health Liveness Check** | API + DB + Redis ping | **PASS** | `{"status":"healthy","database":"healthy","redis":"healthy"}` |
| **Tool Registry Introspection** | Tool reflection & JSON schema | **PASS** | 5 tools returned with full JSON schemas |
| **Multi-Step Analytical Goal** | `sql_readonly` + `calculator` | **PASS** | Real join on orders/products ($3,200), tax calculated ($3,472) |
| **PostgreSQL Persistence** | `runs` and `steps` tables | **PASS** | 1 run record and 6 step records durably inserted in Postgres |
| **Unknown Tool Handling** | Agent ReAct loop | **PASS** | Detected non-existent tool without crash |
| **Bad Arguments Handling** | AST Calculator validator | **PASS** | `Invalid expression syntax` captured and reported |
| **Tool Exception Handling** | AST Calculator ZeroDivisionError | **PASS** | `Division by zero` captured as `ToolResult(ok=False)` |
| **Max-Steps Guardrail** | Loop execution bounds | **PASS** | Terminated at step 10 with `max_steps_exceeded` |
| **Sliding-Window Rate Limiting** | Redis atomic Lua script | **PASS** | 9 of 35 requests rejected with HTTP 429 + `Retry-After: 2` |
| **SQL Injection Defense** | Read-only SQL parser | **PASS** | Non-SELECT `DROP TABLE` blocked with `latency_ms: 0` |
| **Path Traversal Defense** | Sandboxed file reader | **PASS** | `../../etc/passwd` blocked outside base directory |
