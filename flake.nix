{
  description = "PicoSwitch — physical toggle + LCD for a llama.cpp container";

  # nixpkgs is used only for `nix build`, `nix run` and `nix develop` on this
  # flake directly. nixosModules.default deliberately builds its package from
  # the *consumer's* pkgs, so importing the module does not pull a second
  # nixpkgs into your system closure and you can `follows` this away freely.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      # The system wiring: udev rule, dialout membership, and the unit.
      # Usage in a flake-based NixOS config:
      #
      #   inputs.picoswitch.url = "github:bkiu/picoswitch";
      #   ...
      #   modules = [ inputs.picoswitch.nixosModules.default ];
      #
      #   services.picoswitch = {
      #     enable = true;
      #     user = "alice";
      #     containerName = "clef-flash";
      #     composeFile = "/home/alice/projects/clef-flash/docker-compose.yml";
      #   };
      nixosModules.default = ./nix/module.nix;
      nixosModules.picoswitch = ./nix/module.nix;

      packages = forAllSystems (pkgs: rec {
        default = picoswitch-host;
        picoswitch-host = pkgs.callPackage ./nix/package.nix { };
        picoswitch-flash = pkgs.callPackage ./nix/flash.nix { };
        # The MicroPython half as a store path, for flashing a pinned revision.
        firmware = pkgs.callPackage ./nix/firmware.nix { };
      });

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.picoswitch-host}/bin/picoswitch-host";
          meta.description = "Run the PicoSwitch host daemon";
        };
        flash = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.picoswitch-flash}/bin/picoswitch-flash";
          meta.description = "Copy PicoSwitch firmware onto a connected Pico";
        };
      });

      # `nix develop` for firmware work: mpremote for the board, and a python
      # with pyserial so host/picoswitch_host.py runs straight from the tree.
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.mpremote
            (pkgs.python3.withPackages (ps: with ps; [ pyserial ]))
          ];
          shellHook = ''
            echo "picoswitch dev shell"
            echo "  python3 host/picoswitch_host.py --help"
            echo "  mpremote connect /dev/ttyACM0 repl     # ctrl-] to exit"
            echo "  nix run .#flash                        # copy pico/*.py to the board"
          '';
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
