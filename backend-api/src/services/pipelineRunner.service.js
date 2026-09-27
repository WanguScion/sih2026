const { spawn } = require('child_process');

/**
 * Runs the Python pipeline script as a child process, passing INPUT_PATH
 * and OUTPUT_PATH both as CLI arguments and as environment variables
 * (whichever the target script reads is fine). Resolves with
 * { stdout, stderr, code } on a zero exit code; rejects (with stdout/
 * stderr/code attached) otherwise, or on spawn error / timeout.
 */
function runPipeline({ pythonExecutable, scriptPath, inputPath, outputPath, timeoutMs, extraEnv }) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      pythonExecutable,
      [scriptPath, '--input-path', inputPath, '--output-path', outputPath],
      {
        env: {
          ...process.env,
          ...extraEnv,
          INPUT_PATH: inputPath,
          OUTPUT_PATH: outputPath,
        },
      }
    );

    let stdout = '';
    let stderr = '';
    let settled = false;

    const timer = timeoutMs
      ? setTimeout(() => {
          if (settled) return;
          settled = true;
          child.kill('SIGKILL');
          reject(Object.assign(new Error(`Pipeline timed out after ${timeoutMs}ms`), { stdout, stderr }));
        }, timeoutMs)
      : null;

    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });

    child.on('error', (err) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      reject(Object.assign(err, { stdout, stderr }));
    });

    child.on('close', (code) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (code === 0) {
        resolve({ stdout, stderr, code });
      } else {
        reject(Object.assign(new Error(`Pipeline process exited with code ${code}`), { stdout, stderr, code }));
      }
    });
  });
}

module.exports = { runPipeline };
