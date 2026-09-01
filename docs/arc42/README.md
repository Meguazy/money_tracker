# Money Tracker Architecture

This document follows the arc42 template. It describes the current scaffold and
must evolve in the same change as any architectural component.

## 1. Introduction and Goals

### Requirements

Money Tracker will ingest personal spending data, categorize transactions, and
produce weekly, monthly, and yearly insights. The current increment establishes
the local agent runtime and its observability platform without introducing a
transaction-storage design prematurely.

### Quality goals

| Priority | Goal | Meaning |
| --- | --- | --- |
| 1 | Privacy | Prompts, responses, traces, and service data remain local. |
| 2 | Replaceability | The inference engine is replaceable through an HTTP contract. |
| 3 | Traceability | Graph and model activity can be inspected in local Langfuse. |
| 4 | Reproducibility | UV and its lockfile reproduce the Python environment. |
| 5 | Testability | Unit, integration, and end-to-end boundaries are explicit. |

### Stakeholders

- The user owns financial data and operates all services.
- Developers extend graph behavior and future finance components.
- Operators maintain local models, containers, credentials, and volumes.

## 2. Architecture Constraints

- Python 3.12 and UV define the application runtime.
- LangChain and LangGraph define model and orchestration interfaces.
- NVIDIA inference runs out of process and is not installed into the Python
  environment.
- The default inference contract is the OpenAI-compatible HTTP API.
- Langfuse and its data stores are self-hosted; cloud services are not runtime
  dependencies.
- The current target is one Linux workstation with an RTX 5070 Ti and 16 GB
  VRAM.
- Secrets live in a generated, Git-ignored environment file.
- Python code uses Google-style docstrings.
- Tests mirror the source tree and use real services at integration boundaries.

## 3. Context and Scope

### Business context

    User
      |
      | prompt and CLI output
      v
    Money Tracker
      |                         |
      | chat-completions API    | trace ingestion API
      v                         v
    Local inference          Local Langfuse

The user supplies prompts and receives advice. Money Tracker owns orchestration
and the finance-assistant contract. The inference server owns model execution.
Langfuse owns trace ingestion, storage, and visualization.

### Technical interfaces

| Boundary | Protocol | Data |
| --- | --- | --- |
| User to CLI | Process arguments and stdout | Prompt and assistant response |
| App to inference | HTTP OpenAI chat completions | Messages and model settings |
| App to Langfuse | Langfuse SDK over HTTP/OTLP | Graph and model observations |
| Browser to Langfuse | HTTP on localhost:3000 | UI and project administration |
| Browser to MinIO | Signed HTTP on localhost:9090 | Trace media objects |

## 4. Solution Strategy

- Compile a small LangGraph with one assistant node as the first executable
  slice.
- Adapt LangChain ChatOpenAI to a configurable local API instead of linking a
  CUDA runtime into Python.
- Default to llama-server for explicit Blackwell and GGUF tuning while retaining
  compatibility with Ollama and vLLM.
- Attach Langfuse through LangChain callbacks only when local project keys are
  configured.
- Provision the observability platform with Docker Compose and persistent named
  volumes.
- Generate credentials locally with OpenSSL and prohibit committed secrets.

## 5. Building-Block View

### Python application

| Component | Responsibility | Interface and dependencies | Data ownership |
| --- | --- | --- | --- |
| cli | Parse one prompt, invoke the graph, print output, flush traces | Python entry point; graph and observability modules | No persistent data |
| config | Convert environment variables into immutable settings | Standard process environment | Runtime configuration only |
| graph | Define assistant state, system contract, and execution flow | LangGraph and a BaseChatModel | Conversation state for one invocation |
| llm | Construct the OpenAI-compatible model adapter | LangChain ChatOpenAI and config | No persistent data |
| observability | Enable and flush Langfuse callbacks | Langfuse SDK and project keys | Buffered telemetry until flush |

### Local runtime scripts

| Component | Responsibility | Interface and dependencies | Data ownership |
| --- | --- | --- | --- |
| generate_local_env | Generate local application and service credentials | Bash and OpenSSL | The uncommitted .env file |
| run_llama_server | Launch a configured GGUF model with GPU acceleration | Bash and llama-server | Model process; model file remains external |

### Self-hosted services

| Component | Responsibility | Interfaces | Data ownership |
| --- | --- | --- | --- |
| llama-server | Tokenization and GPU model inference | localhost:8080/v1 | In-memory model and KV cache |
| Langfuse web | UI, API, authentication, and trace queries | localhost:3000 | Delegates persistence to its stores |
| Langfuse worker | Asynchronous trace ingestion and processing | Internal Compose network | Queued work while processing |
| PostgreSQL | Transactional Langfuse state and configuration | Internal PostgreSQL protocol | Users, organizations, projects, metadata |
| ClickHouse | Analytical trace storage | Internal HTTP and native protocols | Observations, scores, and trace analytics |
| Redis | Queue and cache operations | Internal Redis protocol | Ephemeral queues and cache state |
| MinIO | S3-compatible blob storage | Internal port 9000; localhost:9090 for media | Events, media, and exported blobs |

## 6. Runtime View

### One assistant turn

1. The CLI reads a prompt and creates optional Langfuse callbacks.
2. The graph receives a HumanMessage.
3. The assistant node prepends the finance system contract.
4. ChatOpenAI sends the messages to the configured local inference API.
5. The inference server executes the model on the GPU and returns a response.
6. LangGraph appends the response and emits callback events.
7. The CLI prints the response and flushes buffered Langfuse events.
8. Langfuse workers persist transactional, analytical, queue, and blob data in
   their respective local stores.

### Startup

1. The environment generator creates application keys and independent service
   secrets.
2. Docker Compose waits for all four data services to report healthy.
3. Langfuse initializes the local organization, project, API keys, and user.
4. llama-server loads the selected GGUF model independently of Compose.
5. The Python process reads the same endpoint and project keys from .env.

## 7. Deployment View

All components run on one Linux workstation.

    Host
    ├── Python process managed by UV
    ├── llama-server process
    │   └── NVIDIA RTX 5070 Ti
    └── Docker Compose network
        ├── Langfuse web       -> 127.0.0.1:3000
        ├── Langfuse worker
        ├── PostgreSQL         -> named volume
        ├── ClickHouse         -> named volumes
        ├── Redis              -> named volume
        └── MinIO              -> 127.0.0.1:9090 and named volume

Database ports are not published. Public host bindings use the loopback
interface. Container images require an initial registry pull, but runtime data
processing does not require cloud services.

## 8. Cross-Cutting Concepts

### Configuration and secrets

Settings enter through environment variables. The committed example contains
placeholders; the generator creates cryptographically random local values.
Compose fails early when a required secret is absent.

### Privacy and network security

Services bind only to localhost, Langfuse telemetry is disabled, the in-app AI
agent is disabled, and no external LLM provider is configured. The API key used
by a local inference server is a compatibility credential, not a cloud secret.

### Observability

LangChain callbacks propagate graph and model events. Tracing is optional:
missing Langfuse keys produce an empty callback list, leaving inference
functional.

### Testing

Unit tests exercise production settings code with the real process environment.
Integration tests use the production HTTP model adapter against a running local
service. End-to-end tests launch the packaged module and traverse the CLI,
graph, adapter, and real model.

### Error handling

Invalid numeric configuration fails with an actionable ValueError. Missing
service credentials make Compose configuration fail. Inference transport and
model errors currently propagate to the CLI, preserving diagnostic detail.

## 9. Architecture Decisions

### AD-001: Out-of-process inference

Status: accepted.

Use an OpenAI-compatible local inference server rather than Python CUDA,
Transformers, or llama-cpp-python dependencies.

Consequences:

- Python dependency resolution remains small and independent of CUDA.
- llama.cpp can be built and tuned directly for compute capability 12.0.
- A local HTTP process must be operated before inference.
- Ollama or vLLM can replace llama-server through configuration.

### AD-002: Self-hosted Langfuse

Status: accepted.

Operate the complete Langfuse stack locally with telemetry disabled.

Consequences:

- Prompts and traces remain under user control.
- PostgreSQL, ClickHouse, Redis, MinIO, web, and worker services consume local
  resources and require backup and upgrade procedures.
- The core application continues without tracing when Langfuse is unavailable
  and its keys are removed.

### AD-003: Headless local initialization

Status: accepted.

Generate credentials once and use the same project keys to initialize Langfuse
and configure the SDK.

Consequences:

- Startup requires no manual cloud or UI provisioning.
- The .env file becomes sensitive local state and must not be committed.
- Existing environments are never overwritten by the generator.

## 10. Quality Requirements

| Scenario | Expected response |
| --- | --- |
| Langfuse keys are absent | The CLI performs inference without trace callbacks. |
| An inference backend changes | Endpoint and model variables change; graph code does not. |
| A numeric setting is invalid | Startup raises an error naming the variable and value. |
| The process exits after a traced call | Buffered Langfuse events are flushed. |
| A database container restarts | Named-volume data remains available. |
| A non-local host connects | Loopback bindings prevent access by default. |
| A developer runs unit tests without services | Unit tests pass; real-service suites report skips. |

## 11. Risks and Technical Debt

- No transaction ingestion, categorization, storage, or reporting component has
  been designed yet.
- The graph is stateless between CLI invocations and has no checkpointer.
- The CLI exposes raw transport exceptions and lacks user-oriented recovery.
- Docker volumes have no automated backup or retention policy.
- Container tags follow major releases rather than immutable image digests.
- The llama.cpp build and GGUF acquisition are documented but not automated.
- The local Langfuse stack has not yet been load-tested on the workstation.
- Integration and end-to-end tests require explicit live-service execution.

## 12. Glossary

| Term | Meaning |
| --- | --- |
| arc42 | Template used to structure this architecture documentation |
| GGUF | Model file format optimized for llama.cpp-style local inference |
| LangGraph | Stateful graph orchestration library used by the application |
| Langfuse | Self-hosted tracing and LLM engineering platform |
| llama-server | llama.cpp HTTP server exposing an OpenAI-compatible API |
| Observation | A traced span, generation, event, or related Langfuse record |
| OpenAI-compatible | HTTP contract compatible with commonly used OpenAI API clients |
| UV | Python project, environment, and lockfile manager |
