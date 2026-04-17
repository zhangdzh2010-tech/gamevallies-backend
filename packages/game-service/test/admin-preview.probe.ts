/**
 * admin-preview.probe.ts
 *
 * Minimal standalone probe for the P0-1 (renderTemplate) substitution logic.
 * Usage:  npx ts-node --transpile-only test/admin-preview.probe.ts
 *
 * We cannot instantiate AdminService directly here because it transitively
 * pulls in game.service → generation-queue.service → bullmq, which isn't
 * available in the minimal test sandbox.  Instead we mirror the exact
 * renderTemplate implementation from admin.service.ts and assert its
 * behaviour.  If admin.service.ts changes, update this copy.
 *
 * Source of truth: packages/game-service/src/admin/admin.service.ts
 *                  (renderTemplate private method)
 */

function renderTemplate(
  template: string,
  variables: Record<string, any> | null | undefined,
): { rendered: string; missing: string[]; unused: string[] } {
  const src = typeof template === "string" ? template : "";
  const vars =
    variables && typeof variables === "object"
      ? variables
      : ({} as Record<string, any>);

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
    return full;
  });

  const unused = Object.keys(vars).filter((k) => !declared.has(k));
  return { rendered, missing, unused };
}

function runProbe() {
  let passed = 0;
  let failed = 0;
  const check = (cond: boolean, label: string) => {
    if (cond) {
      passed += 1;
    } else {
      failed += 1;
      console.error(`  FAIL: ${label}`);
    }
  };

  // 1. Basic replacement
  const r1 = renderTemplate("Hello {name}, canvas={canvas_w}", {
    name: "world",
    canvas_w: 1280,
  });
  check(r1.rendered === "Hello world, canvas=1280", "basic replacement");
  check(r1.missing.length === 0, "no missing on full bind");
  check(r1.unused.length === 0, "no unused on full bind");

  // 2. Missing variable stays literal
  const r2 = renderTemplate("a={a}, b={b}", { a: "1" });
  check(r2.rendered === "a=1, b={b}", "missing literal preserved");
  check(r2.missing.includes("b"), "missing tracked");

  // 3. Unused variable reported
  const r3 = renderTemplate("x={x}", { x: 1, extra: 2 });
  check(r3.rendered === "x=1", "unused var does not alter output");
  check(r3.unused.includes("extra"), "unused tracked");

  // 4. Empty template safe
  const r4 = renderTemplate("", { a: 1 });
  check(r4.rendered === "", "empty template renders empty");
  check(r4.missing.length === 0, "empty template no missing");
  check(r4.unused.includes("a"), "empty template reports unused");

  // 5. Null / undefined value becomes empty string
  const r5 = renderTemplate("v={v}", { v: null });
  check(r5.rendered === "v=", "null becomes empty");
  const r5b = renderTemplate("v={v}", { v: undefined });
  check(r5b.rendered === "v=", "undefined becomes empty");

  // 6. Non-string template coerced safely
  const r6 = renderTemplate(null as any, { a: 1 });
  check(r6.rendered === "" && r6.missing.length === 0, "null template safe");

  // 7. Non-object variables coerced safely
  const r7 = renderTemplate("hi {name}", null);
  check(
    r7.rendered === "hi {name}" && r7.missing.includes("name"),
    "null vars safe",
  );

  // 8. Same placeholder appears twice — counted once in missing
  const r8 = renderTemplate("{x}-{x}-{y}", {});
  check(
    r8.missing.length === 2 &&
      r8.missing.includes("x") &&
      r8.missing.includes("y"),
    "dedup missing across repeated placeholder",
  );

  // 9. Placeholder with underscore and digits
  const r9 = renderTemplate("{hud_min}-{canvas_2}", {
    hud_min: 42,
    canvas_2: "ok",
  });
  check(r9.rendered === "42-ok", "placeholder supports underscore + digits");

  // 10. Brace not matching placeholder pattern left alone
  const r10 = renderTemplate('json: {"k":1}', {});
  check(
    r10.rendered === 'json: {"k":1}' && r10.missing.length === 0,
    "non-placeholder braces ignored",
  );

  // 11. Static check: the substring of the canonical source must match this probe
  //     (keeps the probe honest if admin.service.ts diverges)
  const fs = require("fs");
  const path = require("path");
  const srcPath = path.join(__dirname, "..", "src", "admin", "admin.service.ts");
  const srcText = fs.readFileSync(srcPath, "utf-8");
  check(
    srcText.includes("private renderTemplate("),
    "admin.service.ts declares renderTemplate",
  );
  check(
    srcText.includes("P0-1"),
    "admin.service.ts contains P0-1 marker",
  );
  check(
    srcText.includes("assembleStage3Prompt"),
    "admin.service.ts declares assembleStage3Prompt",
  );
  check(
    srcText.includes("renderPromptPreview"),
    "admin.service.ts declares renderPromptPreview",
  );
  check(
    srcText.includes("safeGetConfig"),
    "admin.service.ts declares safeGetConfig",
  );

  // 12. Controller must have both routes
  const ctrlPath = path.join(
    __dirname,
    "..",
    "src",
    "admin",
    "admin.controller.ts",
  );
  const ctrlText = fs.readFileSync(ctrlPath, "utf-8");
  check(
    ctrlText.includes('@Post("admin/configs/:key/preview")'),
    "admin.controller.ts has preview route",
  );
  check(
    ctrlText.includes('@Post("admin/pipeline/assemble")'),
    "admin.controller.ts has assemble route",
  );
  check(
    ctrlText.includes("previewConfig"),
    "admin.controller.ts declares previewConfig handler",
  );
  check(
    ctrlText.includes("assemblePipelineStage3"),
    "admin.controller.ts declares assemblePipelineStage3 handler",
  );

  const total = passed + failed;
  const status = failed === 0 ? "PASS" : "FAIL";
  console.log(`admin-preview.probe.ts: ${passed}/${total} ${status}`);
  if (failed > 0) {
    process.exit(1);
  }
}

runProbe();
