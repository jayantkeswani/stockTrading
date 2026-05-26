import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  deploymentId: process.env.DEPLOYMENT_ID,
  allowedDevOrigins: ["192.168.*.*", "100.*.*.*"],
};

export default nextConfig;
