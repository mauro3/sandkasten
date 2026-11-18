{
  description = "Nix tools for Sandkasten sessions";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;  # needed for VPN clients, fonts, IDEs, etc.
      };
    in
    {
      packages.${system} = rec {
        sandbox-tools = import ./sandbox-tools.nix { inherit pkgs; };
        default = sandbox-tools;
      };
    };
}
