const { execFileSync } = require('child_process');
const path = require('path');

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return;

  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`,
  );

  // This is not Apple Developer signing. It prevents a fully unsigned bundle,
  // which macOS can report as "damaged" after a GitHub download.
  execFileSync(
    'codesign',
    [
      '--force',
      '--deep',
      '--sign',
      '-',
      appPath,
    ],
    { stdio: 'inherit' },
  );
};
