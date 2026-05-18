import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["192.168.*.*", "100.*.*.*"],
};

export default nextConfig;
