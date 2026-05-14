Name:           dracs
Version:        1.10.0
Release:        %autorelease
Summary:        Dell Rack & Asset Control System - CLI inventory tool for managing Dell bare-metal systems

License:        GPL-3.0-or-later
URL:            https://github.com/kambiz-aghaiepour/dracs
Source:         %{pypi_source dracs}

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros


%global _description %{expand:
Simple, portable, self-contained dynamic CLI inventory tool for managing
Dell bare-metal systems inventory, warranty and lifecycle. Plugs directly
into Dell Support API, provides live hardware data management via SNMP,
and utilizes a portable SQLite database.}

%description %_description

%package -n     python3-dracs
Summary:        %{summary}
Requires:       nginx

%description -n python3-dracs %_description


%prep
%autosetup -p1 -n dracs-%{version}


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l dracs

install -D -m 0644 systemd/dracs-webapp.service %{buildroot}%{_unitdir}/dracs-webapp.service
install -d -m 0755 %{buildroot}%{_sysconfdir}/dracs
install -d -m 0755 %{buildroot}%{_sharedstatedir}/dracs
install -d -m 0755 %{buildroot}%{_localstatedir}/log/dracs


%pre -n python3-dracs
getent group dracs >/dev/null || groupadd -r dracs
getent passwd dracs >/dev/null || \
    useradd -r -g dracs -M -d %{_sharedstatedir}/dracs -s /sbin/nologin \
    -c "DRACS service account" dracs
exit 0


%post -n python3-dracs
%systemd_post dracs-webapp.service

if [ $1 -eq 1 ]; then
    CONF_DIR=%{_sysconfdir}/dracs
    FQDN=$(hostname -f 2>/dev/null || hostname)

    # Seed config files on first install only
    if [ ! -f "${CONF_DIR}/.env" ] && [ ! -f "${CONF_DIR}/drac-passwords.ini" ] && [ ! -f "${CONF_DIR}/BIOS-filename.ini" ]; then
        cd "${CONF_DIR}" && sudo -u dracs %{_bindir}/dracs init 2>/dev/null || :

        # Rename example files to active config files
        [ -f "${CONF_DIR}/.env.example" ] && [ ! -f "${CONF_DIR}/.env" ] && \
            mv "${CONF_DIR}/.env.example" "${CONF_DIR}/.env"
        [ -f "${CONF_DIR}/drac-passwords.ini.example" ] && [ ! -f "${CONF_DIR}/drac-passwords.ini" ] && \
            mv "${CONF_DIR}/drac-passwords.ini.example" "${CONF_DIR}/drac-passwords.ini"
        [ -f "${CONF_DIR}/BIOS-filename.ini.example" ] && [ ! -f "${CONF_DIR}/BIOS-filename.ini" ] && \
            mv "${CONF_DIR}/BIOS-filename.ini.example" "${CONF_DIR}/BIOS-filename.ini"

        chown dracs:dracs "${CONF_DIR}"/.env "${CONF_DIR}"/drac-passwords.ini "${CONF_DIR}"/BIOS-filename.ini 2>/dev/null || :
    fi

    # Deploy nginx configs on first install only
    EXAMPLES_DIR=%{python3_sitelib}/dracs/examples/nginx
    NGINX_DIR=%{_sysconfdir}/nginx/conf.d

    if [ -d "${NGINX_DIR}" ]; then
        if [ ! -f "${NGINX_DIR}/dracs.conf" ] && [ -f "${EXAMPLES_DIR}/dracs.conf.example" ]; then
            cp "${EXAMPLES_DIR}/dracs.conf.example" "${NGINX_DIR}/dracs.conf"
            sed -i "s/dracs\.example\.com/${FQDN}/g" "${NGINX_DIR}/dracs.conf"
        fi

        if [ ! -f "${NGINX_DIR}/dracs_ssl.conf" ] && [ -f "${EXAMPLES_DIR}/dracs_ssl.conf.example" ]; then
            cp "${EXAMPLES_DIR}/dracs_ssl.conf.example" "${NGINX_DIR}/dracs_ssl.conf"
            sed -i "s/dracs\.example\.com/${FQDN}/g" "${NGINX_DIR}/dracs_ssl.conf"
        fi
    fi
fi


%preun -n python3-dracs
%systemd_preun dracs-webapp.service


%postun -n python3-dracs
%systemd_postun_with_restart dracs-webapp.service


%check
%pyproject_check_import


%files -n python3-dracs -f %{pyproject_files}
%{_bindir}/dracs
%{_bindir}/dracs-webapp
%{_unitdir}/dracs-webapp.service
%dir %attr(0755, dracs, dracs) %{_sysconfdir}/dracs
%dir %attr(0755, dracs, dracs) %{_sharedstatedir}/dracs
%dir %attr(0755, dracs, dracs) %{_localstatedir}/log/dracs


%changelog
%autochangelog
