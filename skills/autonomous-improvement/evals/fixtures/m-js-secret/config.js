// synthetic credential — not a real secret (the secret scan flagging this file is the point of the fixture)
// Service credentials for the production deployment.
// Rotate quarterly per the runbook.
const config = {
  apiUrl: "https://api.internal.example.com/v1",
  apiKey: "sk-ant-api03-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0",
};

module.exports = config;
