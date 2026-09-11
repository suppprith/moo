/** @type {import('next').NextConfig} */
// MOO_STATIC_EXPORT=1 builds a plain static site (every route is prerendered and
// the playground talks to the API from the browser), which is what GitHub Pages
// serves. A project page lives under /<repo>, hence the base path.
const staticExport = process.env.MOO_STATIC_EXPORT === "1";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = {
  reactStrictMode: true,
  ...(staticExport ? { output: "export", trailingSlash: true } : {}),
  ...(basePath ? { basePath } : {}),
};

export default nextConfig;
