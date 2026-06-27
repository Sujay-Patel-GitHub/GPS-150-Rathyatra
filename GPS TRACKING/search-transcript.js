import fs from 'fs';
import readline from 'readline';

async function search() {
  const logPath = 'C:\\Users\\janil\\.gemini\\antigravity\\brain\\3b956a5f-45d1-4c1c-aeb2-7275a22216dd\\.system_generated\\logs\\transcript.jsonl';
  if (!fs.existsSync(logPath)) {
    console.error("Log file does not exist at:", logPath);
    return;
  }

  const fileStream = fs.createReadStream(logPath);
  const rl = readline.createInterface({
    input: fileStream,
    crlfDelay: Infinity
  });

  let lineCount = 0;
  for await (const line of rl) {
    lineCount++;
    if (line.includes("rath_yatra_gps_tracker.ino")) {
      try {
        const obj = JSON.parse(line);
        console.log(`Step ${obj.step_index} (Type: ${obj.type}, Source: ${obj.source}):`);
        if (obj.tool_calls) {
          obj.tool_calls.forEach(tc => {
            if (tc.name === 'write_to_file' || tc.name === 'replace_file_content' || tc.name === 'multi_replace_file_content') {
              console.log("  Tool:", tc.name);
              console.log("  Target:", tc.args.TargetFile);
              if (tc.args.Instruction) console.log("  Instruction:", tc.args.Instruction);
              if (tc.args.Description) console.log("  Description:", tc.args.Description);
            }
          });
        }
      } catch (e) {
        // Fallback if line is partially parsed
        console.log(`Line ${lineCount} contains match:`, line.substring(0, 300));
      }
    }
  }
}

search();
