# The host daemon, as a wrapped executable.
#
# callPackage-able on purpose: the NixOS module builds this from the *consumer's*
# pkgs rather than from this flake's own nixpkgs input, so enabling the module
# cannot drag a second nixpkgs into the system closure.
{
  lib,
  stdenvNoCC,
  makeWrapper,
  python3,
}:

let
  pythonEnv = python3.withPackages (ps: with ps; [ pyserial ]);
in
stdenvNoCC.mkDerivation {
  pname = "picoswitch-host";
  version = "0.2.0";

  src = ../host;

  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    install -Dm644 picoswitch_host.py "$out/libexec/picoswitch_host.py"

    # -u so the journal gets the daemon's lines as they happen rather than when
    # a 4 KiB block fills; the useful output is a handful of bytes per switch
    # flip, which would otherwise sit in the buffer indefinitely.
    makeWrapper "${pythonEnv}/bin/python3" "$out/bin/picoswitch-host" \
      --add-flags "-u $out/libexec/picoswitch_host.py"

    runHook postInstall
  '';

  meta = {
    description = "Host daemon bridging a Pico toggle switch to a container runtime";
    homepage = "https://github.com/bkiu/picoswitch";
    mainProgram = "picoswitch-host";
    platforms = lib.platforms.linux;
    license = lib.licenses.mit;
  };
}
