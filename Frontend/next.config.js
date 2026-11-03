/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Keep development bundles separate from production builds. Reusing the
  // same directory can leave webpack-runtime pointing at stale/missing chunks
  // when dev and build are run at different times.
  distDir: process.env.NODE_ENV === 'development' ? '.next-dev' : '.next',
  experimental: {
    forceSwcTransforms: true
  }
};
export default nextConfig;
