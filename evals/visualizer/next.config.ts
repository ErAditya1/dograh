// @ts-nocheck
import type { NextConfig } from "next";

declare const process: { env: Record<string, string | undefined> };

const nextConfig: NextConfig = {
  /* config options here */
  reactCompiler: process.env.NODE_ENV === "production" ? false : undefined,
};

export default nextConfig;
