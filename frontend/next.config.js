/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // deck.gl ships modern JS that Next sometimes needs to compile itself.
  // If you ever see a syntax error coming from inside node_modules/@deck.gl,
  // add the offending package to this list.
  transpilePackages: [
    "deck.gl",
    "@deck.gl/core",
    "@deck.gl/layers",
    "@deck.gl/react",
    "@luma.gl/core",
  ],
};

module.exports = nextConfig;
