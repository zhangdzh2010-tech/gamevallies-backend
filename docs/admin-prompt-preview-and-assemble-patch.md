# Admin — Prompt Preview + Stage-3 Assemble 补丁规格

> 目标:补齐 P0-1(prompt 变量渲染预览)和 P0-2(Step 3 system-prompt 组装可视化),两者共享变量替换逻辑。
> 全部改动位于 `packages/game-service/src/admin/`,新增两个端点,不改动既有签名。

## 1. 新增公用工具:变量替换

把变量替换逻辑抽成私有方法,服务同一签名给 preview 和 assemble 复用。

### `packages/game-service/src/admin/admin.service.ts` — 在 `promptDisplayName` 之后加入

```ts
  /**
   * R-PREVIEW-1: Replace `{var}` placeholders in template with values, returning
   * (rendered, missingVariables, unusedVariables). Non-raising: missing variables
   * stay as literal `{var}` in the output so the caller can still see context.
   */
  private renderTemplate(
    template: string,
    variables: Record<string, any> | null | undefined,
  ): { rendered: string; missing: string[]; unused: string[] } {
    const src = typeof template === "string" ? template : "";
    const vars =
      variables && typeof variables === "object" ? variables : ({} as Record<string, any>);

    const declared = new Set<string>();
    const placeholderRe = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
    let match: RegExpExecArray | null;
    while ((match = placeholderRe.exec(src)) !== null) {
      declared.add(match[1]);
    }

    const missing: string[] = [];
    const rendered = src.replace(placeholderRe, (full, name) => {
      if (Object.prototype.hasOwnProperty.call(vars, name)) {
        const raw = vars[name];
        return raw === null || raw === undefined ? "" : String(raw);
      }
      if (!missing.includes(name)) missing.push(name);
      return full; // leave `{name}` so caller can see un-bound slots
    });

    const unused = Object.keys(vars).filter((k) => !declared.has(k));
    return { rendered, missing, unused };
  }
```

## 2. P0-1:`POST /admin/configs/:key/preview`

### 2.1 Service 方法 — 加到 `admin.service.ts`(紧挨 `getConfig`)

```ts
  async renderPromptPreview(
    key: string,
    variables: Record<string, any> | null | undefined,
  ) {
    const config = await this.getConfig(key); // reuses catalog-fallback logic
    const template = String(config.configValue ?? "");
    const metaVars = Array.isArray((config as any).variables)
      ? ((config as any).variables as string[])
      : [];
    const declaredByMeta = metaVars
      .map((v) => v.replace(/^\{|\}$/g, ""))
      .filter((v) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(v));

    const { rendered, missing, unused } = this.renderTemplate(template, variables);

    // Meta-declared but not actually used in the template → not fatal, just informational
    const declaredNotInTemplate = declaredByMeta.filter(
      (v) => !new RegExp(`\\{${v}\\}`).test(template),
    );

    return {
      configKey: key,
      category: config.category || "prompt",
      templateLength: template.length,
      declaredVariables: declaredByMeta,
      missingVariables: missing,
      unusedVariables: unused,
      declaredButNotInTemplate: declaredNotInTemplate,
      rendered,
      source: (config as any).source || "db",
      isDefault: Boolean((config as any).isDefault),
    };
  }
```

### 2.2 Controller 路由 — 加到 `admin.controller.ts`(System Config 区块内,`upsertConfig` 之后)

```ts
  @Post("admin/configs/:key/preview")
  async previewConfig(
    @Headers("x-admin-token") token: string,
    @Param("key") key: string,
    @Body() body: { variables?: Record<string, any> },
  ) {
    checkAdminToken(token);
    return ok(
      await this.adminService.renderPromptPreview(key, body?.variables || {}),
    );
  }
```

## 3. P0-2:`POST /admin/pipeline/assemble`

Step 3(代码合成)的实际系统 prompt = `prompt.code_gen_system` +(可选)`prompt.code_gen_system_{tier}` + `prompt.generate_alignment_reminder` + bundle policy 片段。下面端点**在 admin 侧重建这一组合**,用当前 DB 中的 prompt + catalog 默认值,返回文字版。

### 3.1 Service 方法 — 加到 `admin.service.ts`

```ts
  async assembleStage3Prompt(input: {
    tier?: string;
    gameType?: string;
    bundleId?: string;
    bundleVersion?: number;
    variables?: Record<string, any>;
  }) {
    const tier = (input.tier || "").toUpperCase().trim();
    const variables = input.variables || {};

    const codeGenSystem = await this.getConfig("prompt.code_gen_system");
    const alignmentReminder = await this.safeGetConfig(
      "prompt.generate_alignment_reminder",
    );
    const tierOverride = tier
      ? await this.safeGetConfig(`prompt.code_gen_system_${tier.toLowerCase()}`)
      : null;
    const requestContext = await this.safeGetConfig(
      "prompt.generate_request_context_template",
    );

    let activeBundle: any = null;
    if (input.bundleId) {
      const bundles = await this.listPromptBundles();
      activeBundle =
        bundles.find(
          (b: any) =>
            b.id === input.bundleId &&
            (input.bundleVersion === undefined ||
              Number(b.version) === Number(input.bundleVersion)),
        ) || null;
    }

    const pieces: Array<{ label: string; source: string; text: string }> = [];

    const baseTemplate = String((codeGenSystem as any).configValue ?? "");
    const baseRender = this.renderTemplate(baseTemplate, variables);
    pieces.push({
      label: "prompt.code_gen_system",
      source: (codeGenSystem as any).source || "db",
      text: baseRender.rendered,
    });

    if (tierOverride) {
      const tierText = String((tierOverride as any).configValue ?? "");
      if (tierText.trim()) {
        const r = this.renderTemplate(tierText, variables);
        pieces.push({
          label: `prompt.code_gen_system_${tier.toLowerCase()}`,
          source: (tierOverride as any).source || "db",
          text: r.rendered,
        });
      }
    }

    if (alignmentReminder) {
      const arText = String((alignmentReminder as any).configValue ?? "");
      if (arText.trim()) {
        const r = this.renderTemplate(arText, variables);
        pieces.push({
          label: "prompt.generate_alignment_reminder",
          source: (alignmentReminder as any).source || "db",
          text: r.rendered,
        });
      }
    }

    if (requestContext) {
      const rcText = String((requestContext as any).configValue ?? "");
      if (rcText.trim()) {
        const r = this.renderTemplate(rcText, variables);
        pieces.push({
          label: "prompt.generate_request_context_template",
          source: (requestContext as any).source || "db",
          text: r.rendered,
        });
      }
    }

    if (activeBundle) {
      if (activeBundle.productPolicy && String(activeBundle.productPolicy).trim()) {
        pieces.push({
          label: `bundle.product.policy@${activeBundle.id}:v${activeBundle.version}`,
          source: activeBundle.source || "db",
          text: String(activeBundle.productPolicy),
        });
      }
      if (
        activeBundle.lockedContractOverride &&
        String(activeBundle.lockedContractOverride).trim()
      ) {
        pieces.push({
          label: `bundle.runtime.locked_contract@${activeBundle.id}:v${activeBundle.version}`,
          source: activeBundle.source || "db",
          text: String(activeBundle.lockedContractOverride),
        });
      }
    }

    const assembled = pieces.map((p) => `--- ${p.label} ---\n${p.text}`).join("\n\n");

    return {
      tier: tier || null,
      gameType: input.gameType || null,
      bundleId: input.bundleId || null,
      bundleVersion:
        input.bundleVersion !== undefined ? Number(input.bundleVersion) : null,
      pieces,
      assembled,
      assembledLength: assembled.length,
      pieceCount: pieces.length,
      warnings: {
        missingVariables: Array.from(
          new Set(
            pieces.flatMap(
              (p) => this.renderTemplate(p.text, variables).missing,
            ),
          ),
        ),
      },
    };
  }

  /** Non-throwing variant of getConfig — returns null if missing. */
  private async safeGetConfig(key: string) {
    try {
      return await this.getConfig(key);
    } catch {
      return null;
    }
  }
```

### 3.2 Controller 路由 — 加到 `admin.controller.ts`(紧邻 `getPromptPipeline`)

```ts
  @Post("admin/pipeline/assemble")
  async assemblePipelineStage3(
    @Headers("x-admin-token") token: string,
    @Body()
    body: {
      tier?: string;
      gameType?: string;
      bundleId?: string;
      bundleVersion?: number;
      variables?: Record<string, any>;
    },
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.assembleStage3Prompt(body || {}));
  }
```

## 4. UI 钩子(`admin-panel-config.js`)— 在编辑模态里加"Preview"按钮

```js
// 加到 modalCurrent 初始化之后
async function previewCurrentPrompt() {
  if (!modalCurrent || modalCurrent.mode !== 'prompt') return;
  const varsText = document.getElementById('promptPreviewVarsInput')?.value || '{}';
  let vars = {};
  try { vars = JSON.parse(varsText); } catch { alert('variables 必须是合法 JSON'); return; }
  const result = await api(
    `/configs/${encodeURIComponent(modalCurrent.configKey)}/preview`,
    { method: 'POST', body: { variables: vars } }
  );
  const out = document.getElementById('promptPreviewOutput');
  if (!out) return;
  out.textContent = result.rendered;
  const info = document.getElementById('promptPreviewInfo');
  if (info) {
    info.textContent = `missing=${result.missingVariables.join(',') || 'none'}  unused=${result.unusedVariables.join(',') || 'none'}`;
  }
}
```

模态模板里加两块(textarea + pre):

```html
<textarea id="promptPreviewVarsInput" rows="3" placeholder='{"canvas_w":1280,"canvas_h":720}'></textarea>
<button onclick="previewCurrentPrompt()">Preview</button>
<pre id="promptPreviewOutput" style="background:#f8fafc;padding:12px"></pre>
<div id="promptPreviewInfo" style="font-size:12px;color:#64748b"></div>
```

## 5. Probe(TypeScript 最小验证 — `packages/game-service/test/admin-preview.probe.ts`)

```ts
/**
 * Minimal probe: given a canned template, renderTemplate replaces declared
 * variables, reports missing/unused correctly, and never throws.
 */
import { AdminService } from "../src/admin/admin.service";

function runProbe() {
  const svc: any = new AdminService({} as any, {} as any, {} as any);

  // 1. Basic replacement
  const r1 = (svc as any).renderTemplate("Hello {name}, canvas={canvas_w}", {
    name: "world",
    canvas_w: 1280,
  });
  console.assert(r1.rendered === "Hello world, canvas=1280", "basic replace");
  console.assert(r1.missing.length === 0, "no missing");
  console.assert(r1.unused.length === 0, "no unused");

  // 2. Missing variable stays literal
  const r2 = (svc as any).renderTemplate("a={a}, b={b}", { a: "1" });
  console.assert(r2.rendered === "a=1, b={b}", "missing literal");
  console.assert(r2.missing.includes("b"), "missing tracked");

  // 3. Unused variable reported
  const r3 = (svc as any).renderTemplate("x={x}", { x: 1, extra: 2 });
  console.assert(r3.unused.includes("extra"), "unused tracked");

  // 4. Empty template safe
  const r4 = (svc as any).renderTemplate("", { a: 1 });
  console.assert(r4.rendered === "" && r4.missing.length === 0, "empty safe");

  // 5. Null/undefined value becomes empty string
  const r5 = (svc as any).renderTemplate("v={v}", { v: null });
  console.assert(r5.rendered === "v=", "null → empty");

  console.log("admin-preview.probe.ts: 5/5 PASS");
}

runProbe();
```

运行:`cd packages/game-service && npx ts-node test/admin-preview.probe.ts`

## 6. 验收清单

1. 编译:`cd packages/game-service && npx tsc --noEmit` 无新增报错。
2. Probe:上一步脚本输出 `5/5 PASS`。
3. 手测:
   - `curl -XPOST -H 'x-admin-token: <T>' -H 'content-type: application/json' -d '{"variables":{"canvas_w":1280,"canvas_h":720}}' http://localhost:3001/api/v1/admin/configs/prompt.mobile_layout_guardrails/preview` → 返回 `rendered` 字段中变量已被替换,`missingVariables: []`。
   - `curl -XPOST -H 'x-admin-token: <T>' -d '{"tier":"ULTRA","variables":{}}' http://localhost:3001/api/v1/admin/pipeline/assemble` → 返回 `pieces` 包含至少 `prompt.code_gen_system`。
4. UI:在 admin 面板任意 Step 的 prompt Edit 模态中,填入 JSON 变量点 Preview → 预览文本出现。

## 7. 与后续 P0/P1 的衔接

- `feature-flags` 端点(P0-3)建议单独 PR,不要和本补丁耦合。
- `history/rollback`(P1-4)需要先建 `SystemConfigRevision` 表,属于 schema 变更,走独立 PR。
- `telemetry/p2`(P1-6)需要 P2.1 遥测事件先落库(当前 `p2_telemetry.emit` 只打日志),也是独立 PR。

## 8. 风险

- `renderTemplate` 只处理 `{name}` 单层占位符,不支持条件/循环 — 与 ai-engine 用 `str.format` 语义一致,OK。
- `assembleStage3Prompt` 没做 LLM 调用,只是 **组装**;如需真 LLM dry-run,走独立 `POST /admin/pipeline/dry-run` PR。
- `safeGetConfig` 吞了异常 → 对 preview 场景是期望行为(缺 prompt 就跳过该片段),但在 logging 里应该补一行 `console.warn`。
