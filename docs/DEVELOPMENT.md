# TimePilot 开发说明

## 运行环境

正式目标 Python 3.12.12。开发者使用已有环境，不在项目内自动创建 Conda 或 venv。根目录 `requirements.txt` 为唯一 Python 依赖入口，`pyproject.toml` 描述打包元数据和安装依赖。两者通过 `python scripts/sync_requirements.py --check` 检查一致性。

前端 Perspective 5.6.1 的依赖放在 `frontend/native-bi/package.json`。Node.js/npm 仅用于从源码构建静态资源，不参与正式 Python/FastAPI 服务运行。

## 修改后的基本检查

```bash
python -m pytest -q
python scripts/sync_requirements.py --check
node --test frontend/native-bi/tests/*.test.mjs
node --check frontend/native-bi/src/main.js
node --check src/timepilot/static/native-bi.js
```

真实验收使用 `TIMEPILOT.sh accept native` / `.bat`。成功的构建资源可重复使用，免去安装前端依赖和重新构建。浏览器步骤输出分阶段日志及 `reports/native_bi_acceptance.json`，不生成截图。

## 代码组织原则

`src/timepilot/` 中的协议、数据、模型、BI 引擎、任务执行、API 应保持单向依赖；前端仅调用公开 REST/WebSocket 接口。新增业务能力优先增加测试和稳定 API，避免创建版本号命名的临时修补模块。不同 UI 页面必须有可刷新路由。修改原始数据必须经过预览、确认并保留可追溯版本。
