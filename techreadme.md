cloudflared tunnel create webfiddle2
cloudflared tunnel route dns --overwrite-dns webfiddle2 live2.webfiddle.net
cloudflared tunnel route dns --overwrite-dns webfiddle2 webfiddle.net # apex?

cloudflared tunnel --url localhost:5769 --name webfiddle2 --protocol http2

# create cloudflared apex domain
cloudflared tunnel route dns --overwrite-dns livew how.nz


