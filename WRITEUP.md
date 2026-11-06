The core objective of this Agent Orchestrator is simple: **handle concurrency, iterative loops, and human intervention without letting one paused agent call lock up the rest of the workspace.**

Rather than setting up complex distributed infrastructure, the implementation uses a single-process, in-memory execution engine to focus entirely on execution semantics, state models, and explicit architectural trade-offs.

---

### 1. Code-based Workflows over Static Configs (JSON/YAML)

* **Context**: Workflows like Generate-QC or Distribution Shaping require dynamic branching, loops, and conditional exits. Modeling these in static YAML or JSON configs quickly becomes unmaintainable.
* **Trade-off**: Building a declarative DAG interpreter requires designing a custom Domain-Specific Language (DSL) up front.
* **Decision**: We chose a **programmatic runtime**. Workflows use native Python control flow (`if`, `while`, loops) wrapped in runtime primitives (`call`, `parallel`, `compute`).
* **Pros**: Extremely flexible, lightweight, and handles dynamic fan-out and iteration effortlessly.
* **Cons**: Defining a new workflow requires writing Python code. If non-developer visual editing becomes a product requirement later, a declarative spec can be built on top of this runtime as a secondary abstraction layer.



---

### 2. State Model: Session, Invocation, and Attempt

The hardest part of agent orchestration is handling mid-execution human intervention. You cannot treat an agent step as a simple synchronous function call.

We separated state into three distinct layers:

* **Session**: Long-lived agent identity and memory context (e.g., keeping context alive across multiple QC revision rounds).
* **Invocation**: The logical node in the workflow execution graph.
* **Attempt**: A single physical run of the underlying agent executor.

**Trade-off & Benefit**: When an agent asks a question, the logical Invocation enters a paused state and immediately releases its thread. Once the user responds, the engine launches a new Attempt. This keeps token accounting accurate per physical call without corrupting the overall logical graph.

---

### 3. Human Intervention and Budget Trade-offs

* **Async Decoupling**: Paused steps free up worker slots instantly. Independent parallel steps in the same run—and all other runs in the workspace—continue executing without interruption.
* **Post-Execution Budgeting**: LLM token usage is only known *after* an inference call finishes. We evaluate budget limits immediately upon return.
* **Trade-off**: If a run breaches its limit, new step registrations are blocked, but currently in-flight parallel calls are allowed to finish and report usage. Total usage might slightly overshoot the configured threshold, but this avoids corrupting state via hard mid-inference terminations.



---

### 4. Splitting Compute Nodes from Agent Nodes

Workflows like Distribution Shaping involve pure logic (e.g., scoring comparisons, bin adjustments, count math).

* **Decision**: We explicitly distinguish between token-consuming agent calls (`call`) and zero-cost deterministic code (`compute`).
* **Benefit**: Math calculations, count target alignments, and bin comparisons run as native Python logic—costing 0 tokens, executing instantly, and operating with 100% determinism. LLMs are used exclusively for actual content rewriting.

---

### 5. Production Priorities

The orchestration semantics are validated. Moving from this POC to a production environment requires adding durability around the existing engine rather than redesigning its architecture:

1. **Durable Storage**: Replace in-memory dictionaries with a transactional database (e.g., PostgreSQL) for workflow state, questions, and attempt logs.
2. **Task Queues & Idempotency**: Use an external task queue (e.g., Temporal or Redis) with worker heartbeats to guarantee idempotent execution when resuming paused steps.
3. **Lifecycle Controls**: Implement explicit timeouts, retries with exponential backoff, and cancellation signals.