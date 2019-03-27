FROM chilipp/empd-admin-base

USER root

COPY ./environment.yml /tmp/environment.yml

RUN conda env update -f /tmp/environment.yml -n base && \
    conda clean --yes --all

ADD ./ /opt/empd-admin

# clone the EMPD-data repository if not existent
RUN git clone --recursive https://github.com/EMPD2/EMPD-data.git /opt/empd-admin/empd_admin/data || :

RUN pip install /opt/empd-admin

COPY run-empd-admin-server.sh /usr/local/bin/run-empd-admin-server
COPY docker_tests.sh /usr/local/bin/test-empd-admin

USER postgres

CMD run-empd-admin-server
