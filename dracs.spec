Name:           dracs
Version:        1.9.5
Release:        %autorelease
Summary:        Dell Rack & Asset Control System - CLI inventory tool for managing Dell bare-metal systems

License:        GPL-3.0-or-later
URL:            https://github.com/kambiz-aghaiepour/dracs
Source:         %{pypi_source dracs}

BuildArch:      noarch
BuildRequires:  python3-devel


%global _description %{expand:
Simple, portable, self-contained dynamic CLI inventory tool for managing
Dell bare-metal systems inventory, warranty and lifecycle. Plugs directly
into Dell Support API, provides live hardware data management via SNMP,
and utilizes a portable SQLite database.}

%description %_description

%package -n     dracs
Summary:        %{summary}

%description -n dracs %_description


%prep
%autosetup -p1 -n dracs-%{version}


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l dracs


%check
%pyproject_check_import


%files -n python3-dracs -f %{pyproject_files}
%{_bindir}/dracs
%{_bindir}/dracs-webapp


%changelog
%autochangelog
