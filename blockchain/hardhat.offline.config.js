// Optional: compile with the npm "solc" (solcjs) package instead of downloading
// the native compiler. Useful on machines where the compiler download is blocked.
const base = require("./hardhat.config.js");
const { subtask } = require("hardhat/config");
const { TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD } = require("hardhat/builtin-tasks/task-names");
subtask(TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD).setAction(async (args, hre, runSuper) => {
  if (args.solcVersion === "0.8.24") {
    return { compilerPath: require.resolve("solc/soljson.js"), isSolcJs: true, version: "0.8.24", longVersion: require("solc/package.json").version };
  }
  return runSuper();
});
module.exports = base;
