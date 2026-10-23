const { getDefaultConfig } = require('expo/metro-config');
const config = getDefaultConfig(__dirname);
// Keep local exports usable on the project's 8 GB Windows development machine.
config.maxWorkers = 1;
module.exports = config;
