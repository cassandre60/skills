const config = require("./config");

async function ping() {
  const response = await fetch(config.apiUrl + "/ping", {
    headers: { Authorization: `Bearer ${config.apiKey}` },
  });
  if (!response.ok) {
    throw new Error(`ping failed: ${response.status}`);
  }
  return response.json();
}

module.exports = { ping };
