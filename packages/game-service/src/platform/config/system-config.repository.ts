import { Prisma } from '@prisma/client';
import { Injectable } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

@Injectable()
export class SystemConfigRepository {
  constructor(private readonly prisma: PrismaService) {}

  async findByCategory(
    category: string,
    client?: PrismaService | Prisma.TransactionClient,
  ): Promise<Array<{ configKey: string; configValue: string }>> {
    const delegate = client ?? this.prisma;
    return delegate.systemConfig.findMany({
      where: { category },
      select: {
        configKey: true,
        configValue: true,
      },
    });
  }

  async findValueByKey(
    configKey: string,
    client?: PrismaService | Prisma.TransactionClient,
  ): Promise<string | null> {
    const delegate = client ?? this.prisma;
    const config = await delegate.systemConfig.findUnique({
      where: { configKey },
      select: { configValue: true },
    });
    return config?.configValue ?? null;
  }
}
