/**
 * Structured tool results — prevents the agent from claiming success
 * without verifiable evidence (diff, files changed, test output).
 */

export interface FileEditResult {
  success: boolean;
  diff: string;
  filesChanged: string[];
  linesAdded: number;
  linesRemoved: number;
  error?: string;
}

export interface ShellExecResult {
  success: boolean;
  exitCode: number;
  stdout: string;
  stderr: string;
  command: string;
  error?: string;
}

export interface GitCommitResult {
  success: boolean;
  commitHash: string;
  testsPassed: boolean;
  lintPassed: boolean;
  diff: string;
  error?: string;
}

/**
 * Build a structured file edit result from a diff string.
 */
export function buildFileEditResult(
  success: boolean,
  diff: string,
  filesChanged: string[],
  error?: string,
): FileEditResult {
  const lines = diff.split("\n");
  let linesAdded = 0;
  let linesRemoved = 0;

  for (const line of lines) {
    if (line.startsWith("+") && !line.startsWith("+++")) linesAdded++;
    if (line.startsWith("-") && !line.startsWith("---")) linesRemoved++;
  }

  return {
    success,
    diff,
    filesChanged,
    linesAdded,
    linesRemoved,
    error,
  };
}

/**
 * Build a structured shell exec result.
 */
export function buildShellExecResult(
  exitCode: number,
  stdout: string,
  stderr: string,
  command: string,
  error?: string,
): ShellExecResult {
  return {
    success: exitCode === 0,
    exitCode,
    stdout,
    stderr,
    command,
    error,
  };
}

/**
 * Build a structured git commit result.
 */
export function buildGitCommitResult(
  success: boolean,
  commitHash: string,
  testsPassed: boolean,
  lintPassed: boolean,
  diff: string,
  error?: string,
): GitCommitResult {
  return {
    success,
    commitHash,
    testsPassed,
    lintPassed,
    diff,
    error,
  };
}
