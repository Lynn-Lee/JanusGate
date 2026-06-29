# JanusGate QA 测试基线

本目录承载 JanusGate 后端测试用例、测试矩阵和覆盖率门禁说明。当前 QA 基线先定义风险覆盖与门禁，后续由测试执行角色在同目录补充 pytest 用例。

- 测试矩阵：[`QA_MATRIX.md`](QA_MATRIX.md)
- 覆盖率门禁：[`COVERAGE_GATE.md`](COVERAGE_GATE.md)

本目录约束：
1. 后端测试默认在 `backend/` 下运行：`pytest`。
2. API 测试优先使用 FastAPI `TestClient` 或 `httpx` ASGI 客户端，不依赖外部网络。
3. 涉及认证、授权、加密、审计、审批流的测试必须包含正常路径、失败路径和边界路径。
4. CI 接入由 DevOps owner 负责；QA 只维护门禁标准与测试覆盖要求。
