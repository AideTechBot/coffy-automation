{
  description = "Coffee machine smart-plug tray app (Kasa EP10)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));

      pythonEnv = pkgs: pkgs.python3.withPackages (ps: [
        ps.pyqt6
        ps.aiohttp
        ps.keyring
        ps.secretstorage
        ps.jeepney
      ]);
    in {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pythonEnv pkgs) ];
        };
      });

      packages = forAllSystems (pkgs: {
        default = pkgs.writeShellScriptBin "coffy" ''
          exec ${pythonEnv pkgs}/bin/python ${./coffy.py} "$@"
        '';
        set-credentials = pkgs.writeShellScriptBin "coffy-set-credentials" ''
          exec ${pythonEnv pkgs}/bin/python ${./set_credentials.py} "$@"
        '';
      });
    };
}
