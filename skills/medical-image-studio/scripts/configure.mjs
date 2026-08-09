#!/usr/bin/env node
import { chmod, mkdir, writeFile } from "node:fs/promises";
import process from "node:process";
import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { CONFIG_DIR, CONFIG_PATH, maskKey, readSavedConfig } from "./lib/providers.mjs";

async function askHidden(label) {
  if (!input.isTTY || !output.isTTY) {
    throw new Error("当前不是可交互终端。请在本机终端直接运行 configure.mjs，或使用环境变量配置 Key。");
  }
  output.write(label);
  input.setRawMode(true);
  input.resume();
  input.setEncoding("utf8");
  let value = "";
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      input.setRawMode(false);
      input.pause();
      input.off("data", onData);
      output.write("\n");
    };
    const onData = (char) => {
      if (char === "\u0003") {
        cleanup();
        reject(new Error("已取消配置。"));
      } else if (char === "\r" || char === "\n") {
        cleanup();
        resolve(value.trim());
      } else if (char === "\u007f" || char === "\b") {
        value = value.slice(0, -1);
      } else if (char >= " ") {
        value += char;
      }
    };
    input.on("data", onData);
  });
}

async function main() {
  if (process.argv.includes("--show")) {
    const saved = await readSavedConfig();
    const provider = saved.provider || "未配置";
    const providerConfig = saved.providers?.[saved.provider] || {};
    console.log(JSON.stringify({
      provider,
      model: providerConfig.model || "未配置",
      key: maskKey(providerConfig.apiKey),
      configPath: CONFIG_PATH,
    }, null, 2));
    return;
  }

  const rl = readline.createInterface({ input, output });
  console.log("医疗内容生图 Skill：首次配置（Key 只保存在本机用户目录）\n");
  const providerAnswer = (await rl.question("选择服务商：1=HiAPI（推荐）  2=老张 API  [1]：")).trim() || "1";
  const provider = providerAnswer === "2" ? "laozhang" : "hiapi";
  let model = "gpt-image-2/text-to-image";
  let supportsControls = true;

  if (provider === "laozhang") {
    const route = (await rl.question("老张 Key 类型：1=默认组按次/VIP（推荐）  2=官方用量组  3=默认标准组  [1]：")).trim() || "1";
    if (route === "2") {
      model = "gpt-image-2";
      supportsControls = true;
    } else if (route === "3") {
      model = "gpt-image-2";
      supportsControls = false;
    } else {
      model = "gpt-image-2-vip";
      supportsControls = true;
    }
  }
  rl.close();

  const apiKey = await askHidden(`粘贴 ${provider === "hiapi" ? "HiAPI" : "老张 API"} Key（输入不回显）：`);
  if (!apiKey) throw new Error("Key 不能为空。");

  const saved = await readSavedConfig();
  const next = {
    version: 1,
    provider,
    providers: {
      ...(saved.providers || {}),
      [provider]: {
        apiKey,
        model,
        supportsControls,
        baseUrl: provider === "hiapi" ? "https://api.hiapi.ai" : "https://api.laozhang.ai/v1",
      },
    },
  };
  await mkdir(CONFIG_DIR, { recursive: true, mode: 0o700 });
  await writeFile(CONFIG_PATH, `${JSON.stringify(next, null, 2)}\n`, { mode: 0o600 });
  try { await chmod(CONFIG_PATH, 0o600); } catch { /* Windows may ignore POSIX permissions. */ }
  console.log(`配置完成：${provider} / ${model}`);
  console.log(`配置文件：${CONFIG_PATH}`);
  console.log("完整 Key 未显示，也没有写入 Skill 文件夹。现在可以直接让 Agent 生图。 ");
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
