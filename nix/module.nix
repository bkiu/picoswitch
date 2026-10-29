# NixOS module for PicoSwitch.
#
# Exists because none of the README's generic setup steps work on NixOS:
# /etc/udev/rules.d is a read-only symlink into the store, there is no global
# site-packages to `pip install pyserial` into, and units are declared rather
# than copied into /etc/systemd/system. This module does all three declaratively.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.picoswitch;
in
{
  options.services.picoswitch = {
    enable = lib.mkEnableOption "the PicoSwitch host daemon";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ./package.nix { }";
      description = "The picoswitch-host package to run.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      example = "alice";
      description = ''
        Account the daemon runs as. It is added to `dialout` (to open the
        Pico's CDC-ACM port) and must already be able to reach the container
        runtime — membership of `docker`, or a rootless podman socket.

        Deliberately not root: the daemon's whole job is to shell out to
        `compose up`/`down`, and there is no reason for that to happen with
        more privilege than the human flipping the switch has.
      '';
    };

    containerName = lib.mkOption {
      type = lib.types.str;
      default = "clef-flash";
      description = ''
        Container to watch, matched exactly. Must equal the runtime's
        `container_name` (or the compose-generated name) or the daemon will
        report the container as permanently down: a mismatch here is silent,
        because "no such container" and "container is stopped" look identical
        through `ps`.
      '';
    };

    composeFile = lib.mkOption {
      type = lib.types.str;
      example = "/home/alice/projects/clef-flash/docker-compose.yml";
      description = ''
        Compose file the switch drives. A string rather than a path: pointing
        this at a working checkout in `$HOME` is the normal case, and a path
        would copy it into the store and freeze the version you happened to
        rebuild with.
      '';
    };

    serialPort = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_e66440a2cb6b5e2b-if00";
      description = ''
        Serial port to use. Null auto-detects, preferring the stable
        `/dev/serial/by-id/*MicroPython*` symlink over `/dev/ttyACM*`, whose
        numbering shifts as other CDC-ACM devices come and go.
      '';
    };

    verbose = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Log every two-second `CMD:STATUS` poll, not just switch flips. Useful
        to confirm the board is alive; noisy enough to fill the journal if
        left on.
      '';
    };

    installFlashTool = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Install `picoswitch-flash` and `mpremote` system-wide. mpremote is the
        only way to read a traceback out of the board's `main.py`, so this is
        on by default.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    # Merges with any other extraGroups definition for this user.
    users.users.${cfg.user}.extraGroups = [ "dialout" ];

    # 2e8a is Raspberry Pi's vendor ID. 0005 is a Pico running MicroPython
    # (CDC-ACM); 0003 is the RP2 bootloader, which appears as a mass-storage
    # device when the board is plugged in holding BOOTSEL — that is how
    # MicroPython itself gets flashed, so it wants the same access.
    #
    # GROUP+MODE rather than the usual MODE="0666": world-writable serial is a
    # needlessly broad grant, and `dialout` membership is required for the
    # interactive mpremote workflow anyway, so 0666 buys nothing.
    services.udev.extraRules = ''
      SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", ATTRS{idProduct}=="0005", GROUP="dialout", MODE="0660", TAG+="picoswitch"
      SUBSYSTEM=="block", ATTRS{idVendor}=="2e8a", ATTRS{idProduct}=="0003", GROUP="dialout", MODE="0660"
    '';

    environment.systemPackages =
      [ cfg.package ]
      ++ lib.optionals cfg.installFlashTool [
        pkgs.mpremote
        (pkgs.callPackage ./flash.nix { })
      ];

    systemd.services.picoswitch-host = {
      description = "PicoSwitch host daemon (physical toggle for ${cfg.containerName})";
      after = [ "network.target" "docker.service" ];
      wants = [ "docker.service" ];
      wantedBy = [ "multi-user.target" ];

      # The daemon shells out to the container runtime and to nvidia-smi. Both
      # come from the system profile, and taking them from there rather than
      # pinning store paths keeps the unit tracking
      # virtualisation.docker.package and the nvidia driver across rebuilds.
      # Set via `path` so it is prepended to the PATH NixOS generates, rather
      # than a second Environment=PATH= that would depend on override ordering.
      path = [ "/run/current-system/sw" ];

      serviceConfig = {
        Type = "simple";
        User = cfg.user;
        SupplementaryGroups = [ "dialout" ];

        ExecStart = lib.concatStringsSep " " (
          [
            "${cfg.package}/bin/picoswitch-host"
            "--container" cfg.containerName
            "--compose-file" cfg.composeFile
          ]
          ++ lib.optionals (cfg.serialPort != null) [ "--port" cfg.serialPort ]
          ++ lib.optional cfg.verbose "--verbose"
        );

        # always, not on-failure: the daemon exits non-zero when the compose
        # file is missing, and that is exactly the case where retrying is right
        # (a half-checked-out working tree). An absent or replugged Pico it
        # handles itself by polling for the port, so this is only a backstop.
        Restart = "always";
        RestartSec = 5;

        # Modest hardening. No NoNewPrivileges/ProtectSystem=strict: the daemon
        # talks to the runtime's socket and reads a compose file out of $HOME,
        # both of which those options break.
        PrivateTmp = true;
        ProtectKernelTunables = true;
        ProtectControlGroups = true;
        RestrictSUIDSGID = true;
      };
    };
  };
}
