import { Controller, Get, Header, Res } from '@nestjs/common';
import { Response } from 'express';
import * as fs from 'fs';
import * as path from 'path';

@Controller('game-shell')
export class GameShellController {
  private shellHtml: string;

  constructor() {
    const htmlPath = path.join(__dirname, 'game-shell.html');
    this.shellHtml = fs.readFileSync(htmlPath, 'utf-8');
  }

  @Get('index.html')
  @Header('Content-Type', 'text/html; charset=utf-8')
  @Header('Cache-Control', 'no-cache, no-store, must-revalidate')
  @Header('X-Frame-Options', 'SAMEORIGIN')
  getShell(@Res() res: Response) {
    res.send(this.shellHtml);
  }
}
