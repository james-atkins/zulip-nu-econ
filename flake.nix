{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-23.05";
  };

  outputs = { self, nixpkgs }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [ "x86_64-linux" ];

      pkgs = forAllSystems (system:
        import nixpkgs {
          inherit system;
          hostPlatform = system;
        }
      );

      pypkgs-core = forAllSystems (system:
        ps: with ps; [
          beautifulsoup4
          feedparser
          jinja2
          lxml
          markdownify
          pytz
          requests
          zulip
        ]
      );

      pypkgs-dev = forAllSystems (system:
        ps: with ps; [
          types-pytz
          types-requests

          pylsp-mypy
          pylsp-rope
          python-lsp-server
          ruff-lsp
        ]
      );

      nuzulip = forAllSystems (system:
        pkgs.${system}.stdenv.mkDerivation {
          name = "nuzulip";
          buildInputs = [ (pkgs.${system}.python310.withPackages pypkgs-core.${system}) ];
          dontUnpack = true;
          installPhase = ''
            mkdir $out
            cp -r ${./.}/* $out

            mkdir $out/bin
            ln -s $out/welcome-bot/main.py $out/bin/welcome-bot
            ln -s $out/events-bot/main.py $out/bin/events-bot
            ln -s $out/working-papers-bot/main.py $out/bin/working-papers-bot
          '';
        }
      );
    in
    {
      formatter = forAllSystems (system: pkgs.${system}.nixpkgs-fmt);

      packages = forAllSystems (system: {
        nuzulip = nuzulip.${system};
      });

      devShells = forAllSystems (system: {
        default = with pkgs.${system}; mkShell {
          nativeBuildInputs = [
            (python3.withPackages (ps: (pypkgs-core.${system} ps) ++ (pypkgs-dev.${system} ps)))
          ];
        };
      });

      nixosModules.default = { config, lib, pkgs, ... }:
        let
          system = pkgs.stdenv.hostPlatform.system;
          nuzulip' = nuzulip.${system};

          cfg = config.nuzulip;

          makeTimer = name: exec-start: zuliprc: on-calendar:
            {
              systemd.services."nuzulip-${name}" = {
                after = [ "network.target" ];
                serviceConfig = {
                  Type = "oneshot";
                  ExecStart = exec-start;
                  LoadCredential = "zuliprc:${zuliprc}";
                  Environment = "ZULIPRC=%d/zuliprc";

                  User = "zulip-bot";
                  Group = "zulip-bot";
                  DynamicUser = true;
                  ProtectHome = true;
                  PrivateDevices = true;
                  ProtectKernelTunables = true;
                  ProtectControlGroups = true;
                };
              };

              systemd.timers."nuzulip-${name}" = {
                wantedBy = [ "timers.target" ];
                timerConfig = {
                  OnCalendar = on-calendar;
                  Persistent = true;
                  RandomizedDelaySec = 60;
                };
              };
            };
        in
        {
          options.nuzulip = {
            enable = lib.mkEnableOption "Enable Northwestern Economics Zulip bots";
            zuliprc.welcome-bot = lib.mkOption {
              type = lib.types.path;
            };
            zuliprc.calendar-bot = lib.mkOption {
              type = lib.types.path;
            };
            zuliprc.working-papers-bot = lib.mkOption {
              type = lib.types.path;
            };
          };
          config = lib.mkIf cfg.enable (lib.mkMerge [
            {
              systemd.services."nuzulip-welcome-bot" = {
                after = [ "network.target" ];
                wantedBy = [ "multi-user.target" ];
                serviceConfig = {
                  ExecStart = "${nuzulip'}/bin/welcome-bot";
                  Restart = "always";
                  LoadCredential = "zuliprc:${cfg.zuliprc.welcome-bot}";
                  Environment = "ZULIPRC=%d/zuliprc";

                  User = "zulip-bot";
                  Group = "zulip-bot";
                  DynamicUser = true;
                  ProtectHome = true;
                  PrivateDevices = true;
                  ProtectKernelTunables = true;
                  ProtectControlGroups = true;
                };
              };
            }
            (makeTimer "events-daily" "${nuzulip'}/bin/events-bot daily" cfg.zuliprc.calendar-bot "08:00")
            (makeTimer "events-weekly" "${nuzulip'}/bin/events-bot weekly" cfg.zuliprc.calendar-bot "Mon 08:00")
            (makeTimer "nber-working-papers" "${nuzulip'}/bin/working-papers-bot" cfg.zuliprc.working-papers-bot "Mon 08:00")
          ]);
        };
    };
}
