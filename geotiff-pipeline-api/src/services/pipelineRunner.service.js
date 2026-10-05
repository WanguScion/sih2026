const { spawn } = require('child_process');
const path = require('path');

function runPipeline({ scriptPath, inputPath, outputPath, timeoutMs, extraEnv }) {
  return new Promise((resolve, reject) => {
    const pythonExecutable = path.resolve(
      __dirname,
      '../../../geotiff_segformer_gis/.venv/bin/python'
    );

    const child = spawn(
      pythonExecutable,
      [scriptPath, '--input-path', inputPath, '--output-path', outputPath],
      {
        env: {
          ...process.env,
          ...extraEnv,
          INPUT_PATH: inputPath,
          OUTPUT_PATH: outputPath,
          VIRTUAL_ENV: path.dirname(path.dirname(pythonExecutable)),
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
          reject(Object.assign(
            new Error(`Pipeline timed out after ${timeoutMs}ms`),
            { stdout, stderr }
          ));
        }, timeoutMs)
      : null;

    child.stdout.on('data', chunk => {
      stdout += chunk.toString();
    });

    child.stderr.on('data', chunk => {
      stderr += chunk.toString();
    });

    child.on('error', err => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      reject(Object.assign(err, { stdout, stderr }));
    });

    child.on('close', code => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);

      if (code === 0) {
        resolve({ stdout, stderr, code });
      } else {
        reject(Object.assign(
          new Error(`Pipeline process exited with code ${code}`),
          { stdout, stderr, code }
        ));
      }
    });
  });
}

module.exports = { runPipeline };
