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
    in
    {
      formatter = forAllSystems (system: pkgs.${system}.nixpkgs-fmt);

      devShells = forAllSystems (system: {
        default = with pkgs.${system}; mkShell {
          nativeBuildInputs = [
            (python3.withPackages (ps: with ps; [ beautifulsoup4 lxml jinja2 requests zulip ]))
          ];
        };
      });
    };
}
