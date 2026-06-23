Name:           dracs
Version:        2.6.1
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


%package -n     python3-dracs-libs
Summary:        DRACS Python libraries

%description -n python3-dracs-libs
Python library packages for DRACS. Contains the dracs and dracs_client
Python modules with shared display logic, database models, and client code.


%package -n     python3-dracs
Summary:        %{summary}
Requires:       python3-dracs-libs = %{version}-%{release}
Requires:       nginx
Requires:       openssl
Requires:       python3-websockify
Requires:       tftp-server
Requires:       tftp
Provides:       user(dracs)
Provides:       group(dracs)

%description -n python3-dracs %_description


%package -n     dracs-client
Summary:        DRACS remote client CLI
Requires:       python3-dracs-libs = %{version}-%{release}

%description -n dracs-client
Remote CLI client for querying a DRACS server inventory.
Connects to the DRACS web application over HTTPS.


%prep
%autosetup -p1 -n dracs-%{version}


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l dracs dracs_client

install -D -m 0644 systemd/dracs-webapp.service %{buildroot}%{_unitdir}/dracs-webapp.service
install -d -m 0755 %{buildroot}%{_sysconfdir}/dracs
install -d -m 0755 %{buildroot}%{_sharedstatedir}/dracs
install -d -m 0755 %{buildroot}%{_sharedstatedir}/dracs/web/firmware
install -d -m 0755 %{buildroot}%{_sharedstatedir}/dracs/web/bios
install -d -m 0755 %{buildroot}%{_sharedstatedir}/dracs/web/tsr
install -d -m 0755 %{buildroot}%{_sharedstatedir}/dracs/web/iso
install -d -m 0755 %{buildroot}%{_sharedstatedir}/dracs/archive/firmware
install -d -m 0755 %{buildroot}%{_sharedstatedir}/dracs/archive/bios
install -d -m 0755 %{buildroot}%{_localstatedir}/log/dracs
install -D -m 0644 logrotate/dracs %{buildroot}%{_sysconfdir}/logrotate.d/dracs


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
        cd "${CONF_DIR}" && sudo -u dracs %{_bindir}/dracs init 1>/dev/null 2>&1 || :

        # Rename example files to active config files
        [ -f "${CONF_DIR}/.env.example" ] && [ ! -f "${CONF_DIR}/.env" ] && \
            mv "${CONF_DIR}/.env.example" "${CONF_DIR}/.env"
        [ -f "${CONF_DIR}/drac-passwords.ini.example" ] && [ ! -f "${CONF_DIR}/drac-passwords.ini" ] && \
            mv "${CONF_DIR}/drac-passwords.ini.example" "${CONF_DIR}/drac-passwords.ini"
        [ -f "${CONF_DIR}/BIOS-filename.ini.example" ] && [ ! -f "${CONF_DIR}/BIOS-filename.ini" ] && \
            mv "${CONF_DIR}/BIOS-filename.ini.example" "${CONF_DIR}/BIOS-filename.ini"

        chown dracs:dracs "${CONF_DIR}"/.env "${CONF_DIR}"/drac-passwords.ini "${CONF_DIR}"/BIOS-filename.ini 2>/dev/null || :

        # Create system-wide config from .env
        [ -f "${CONF_DIR}/.env" ] && [ ! -f "${CONF_DIR}/dracs.conf" ] && \
            cp "${CONF_DIR}/.env" "${CONF_DIR}/dracs.conf" && \
            chown dracs:dracs "${CONF_DIR}/dracs.conf"
        # cleanup .env
        [ -f "${CONF_DIR}/dracs.conf" ] && [ -f "${CONF_DIR}/.env" ] && \
            rm -f "${CONF_DIR}/.env"
    fi

    # Edit config for flask secret and db path
    FLASK_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/^FLASK_SECRET_KEY=.*/FLASK_SECRET_KEY=${FLASK_SECRET}/g" "${CONF_DIR}/dracs.conf"

    DRACS_DB=%{_sharedstatedir}/dracs/warranty.db
    sed -i "s,^DRACS_DB=.*,DRACS_DB=${DRACS_DB},g" "${CONF_DIR}/dracs.conf"

    # initialize db if non-existent
    [ ! -f "$DRACS_DB" ] && sudo -u dracs %{_bindir}/dracs li 1>/dev/null 2>&1 || :

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

    # Generate self-signed SSL certificate if not present
    CERT_DIR=/etc/pki/tls/certs
    if [ ! -f "${CERT_DIR}/${FQDN}.pem" ] || [ ! -f "${CERT_DIR}/${FQDN}.key" ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "${CERT_DIR}/${FQDN}.key" \
            -out "${CERT_DIR}/${FQDN}.pem" \
            -subj "/CN=${FQDN}" 2>/dev/null || :
        chmod 0600 "${CERT_DIR}/${FQDN}.key" 2>/dev/null || :
    fi

    # Configure TFTP server for iDRAC write access
    if [ -f /usr/lib/systemd/system/tftp.service ] && [ ! -f /etc/systemd/system/tftp.service ]; then
        cp /usr/lib/systemd/system/tftp.service /etc/systemd/system/tftp.service
        sed -i 's|^ExecStart=.*|ExecStart=/usr/sbin/in.tftpd -c -p -s /var/lib/tftpboot|' \
            /etc/systemd/system/tftp.service
    fi
    systemctl daemon-reload 2>/dev/null || :
    systemctl enable --now tftp.socket 2>/dev/null || :
    systemctl restart tftp.service 2>/dev/null || :
    chmod 777 /var/lib/tftpboot 2>/dev/null || :

    # SELinux booleans for TFTP
    setsebool -P tftp_home_dir 1 2>/dev/null || :
    setsebool -P tftp_anon_write 1 2>/dev/null || :
fi

# Open firewall ports 80, 443, and TFTP
if command -v firewall-cmd &>/dev/null && systemctl is-enabled firewalld &>/dev/null; then
    firewall-cmd --permanent --add-port=80/tcp 2>/dev/null || :
    firewall-cmd --permanent --add-port=443/tcp 2>/dev/null || :
    firewall-cmd --permanent --add-service=tftp 2>/dev/null || :
    firewall-cmd --reload 2>/dev/null || :
elif systemctl is-enabled iptables &>/dev/null; then
    iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || \
        iptables -I INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || :
    iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || \
        iptables -I INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || :
    modprobe nf_conntrack_tftp 2>/dev/null || :
    iptables -C INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
        iptables -I INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || :
    iptables -C INPUT -p udp --dport 69 -j ACCEPT 2>/dev/null || \
        iptables -I INPUT -p udp --dport 69 -j ACCEPT 2>/dev/null || :
    service iptables save 2>/dev/null || :
fi


%preun -n python3-dracs
%systemd_preun dracs-webapp.service


%postun -n python3-dracs
%systemd_postun_with_restart dracs-webapp.service


%check
%pyproject_check_import


%files -n python3-dracs-libs -f %{pyproject_files}


%files -n python3-dracs
%{_bindir}/dracs
%{_bindir}/dracs-webapp
%{_unitdir}/dracs-webapp.service
%dir %attr(0755, dracs, dracs) %{_sysconfdir}/dracs
%dir %attr(0755, dracs, dracs) %{_sharedstatedir}/dracs
%dir %attr(0755, dracs, dracs) %{_sharedstatedir}/dracs/web
%dir %attr(0755, dracs, dracs) %{_sharedstatedir}/dracs/web/firmware
%dir %attr(0755, dracs, dracs) %{_sharedstatedir}/dracs/web/bios
%dir %attr(0755, dracs, dracs) %{_sharedstatedir}/dracs/web/tsr
%dir %attr(0755, dracs, dracs) %{_sharedstatedir}/dracs/web/iso
%dir %attr(0755, dracs, dracs) %{_sharedstatedir}/dracs/archive
%dir %attr(0755, dracs, dracs) %{_sharedstatedir}/dracs/archive/firmware
%dir %attr(0755, dracs, dracs) %{_sharedstatedir}/dracs/archive/bios
%dir %attr(0755, dracs, dracs) %{_localstatedir}/log/dracs
%config(noreplace) %{_sysconfdir}/logrotate.d/dracs


%files -n dracs-client
%{_bindir}/dracs-client


%changelog
%autochangelog
