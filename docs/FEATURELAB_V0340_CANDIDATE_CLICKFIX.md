# TimePilot v0.34.0 特征候选交互修复（UI1）

## 修改范围

- 特征名称和该行的正文改为可点击区域，直接切换右侧特征详情及真实图表。移除独立的“查看”文字按钮。
- 复选框与特征查看按钮改为同级元素，避免 HTML `label` 让点击名称误触发勾选。
- 仅复选框的 `change` 事件会修改当前候选选择；保存选择仍由现有提交操作完成。
- 点击当前特征时不重复请求图表，避免无意义刷新。
- 当前查看项保留高亮，新增 `aria-current`、`aria-controls`、复选框独立名称和键盘可访问的焦点样式。
- 改动文件的静态资源 URL 带 `0.34.0-ui1` 缓存版本，避免浏览器继续使用旧 HTML/JS/CSS。

## 验收项目

1. 点击任意特征名称或指标所在的按钮区域，右侧显示该项的详情；不会切换复选框。
2. 点击复选框，仅改变对应勾选状态与“已选”数量；不会切换右侧详情。
3. 在筛选为“通过”等条件时，保存选择仍保留其他筛选条件下已选择的特征。
4. 点击当前高亮特征不会触发多余的图表请求和历史记录。
5. 键盘 `Enter` 可查看当前特征，复选框上按 `Space` 可勾选或取消。
6. 页面刷新后继续依据既有 URL 参数恢复当前特征、筛选条件和图表模式。

## 测试

```bash
node --test frontend/native-bi/tests/featurelab340_candidate_clickfix.test.mjs
node --test frontend/native-bi/tests/*.test.mjs
python scripts/verify_installation.py --source-only --strict-source
```

修改仅涉及前端静态页面、样式与事件处理，未修改数据库、特征算法及后端 API。此交互修复不要求重建 Perspective bundle；如果部署环境使用反向代理/CDN 缓存，应确认 HTML 已更新。
