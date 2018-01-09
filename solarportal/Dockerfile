ARG BUILD_FROM
FROM $BUILD_FROM

#add env
ENV LANG C.UTF-8

# Setup base
RUN apk add --no-cache python3 && \
    python3 -m ensurepip && \
    rm -r /usr/lib/python*/ensurepip && \
    pip3 install --upgrade pip setuptools && \
    if [ ! -e /usr/bin/pip ]; then ln -s pip3 /usr/bin/pip ; fi && \
    if [[ ! -e /usr/bin/python ]]; then ln -sf /usr/bin/python3 /usr/bin/python; fi && \
    rm -r /root/.cache

RUN apk add --no-cache \
    jq \
    py3-lxml

RUN pip3 install --upgrade \
    aiohttp \
    beautifulsoup4 \
    click \
    voluptuous \
    hbmqtt

WORKDIR /data

# Copy data for add-on
COPY run.sh /
COPY solarportal.py /usr/local/bin/
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]